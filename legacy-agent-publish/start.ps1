# Quick Start Script for Azure OpenAI Chatbot
# This script starts both backend and frontend services

# Trap Ctrl+C to clean up processes
$script:BackendJob = $null
$script:FrontendJob = $null
$script:TeamsAgentJob = $null
$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Cleanup {
    Write-Host "`n`nShutting down services..." -ForegroundColor Yellow
    
    if ($script:BackendJob) {
        Write-Host "Stopping backend..." -ForegroundColor Yellow
        Stop-Job -Job $script:BackendJob -ErrorAction SilentlyContinue
        Remove-Job -Job $script:BackendJob -Force -ErrorAction SilentlyContinue
    }
    
    if ($script:FrontendJob) {
        Write-Host "Stopping frontend..." -ForegroundColor Yellow
        Stop-Job -Job $script:FrontendJob -ErrorAction SilentlyContinue
        Remove-Job -Job $script:FrontendJob -Force -ErrorAction SilentlyContinue
    }
    
    if ($script:TeamsAgentJob) {
        Write-Host "Stopping Teams agent..." -ForegroundColor Yellow
        Stop-Job -Job $script:TeamsAgentJob -ErrorAction SilentlyContinue
        Remove-Job -Job $script:TeamsAgentJob -Force -ErrorAction SilentlyContinue
    }
    
    # Kill any remaining uvicorn, streamlit, and python processes
    Get-Process | Where-Object { $_.ProcessName -like "*uvicorn*" -or $_.ProcessName -like "*streamlit*" -or $_.ProcessName -like "*python*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    
    Write-Host "✓ All services stopped" -ForegroundColor Green
    exit 0
}

# Register cleanup on Ctrl+C
Register-EngineEvent PowerShell.Exiting -Action { Cleanup } | Out-Null

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Azure OpenAI Chatbot Launcher" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Ensure script runs from its own directory
Set-Location $script:ScriptRoot

# Check if uv is installed
Write-Host "Checking for uv package manager..." -ForegroundColor Yellow
if (!(Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: uv is not installed!" -ForegroundColor Red
    Write-Host "Please install uv first:" -ForegroundColor Yellow
    Write-Host "  irm https://astral.sh/uv/install.ps1 | iex" -ForegroundColor White
    exit 1
}
Write-Host "✓ uv found" -ForegroundColor Green

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
uv sync --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "ERROR: Failed to install dependencies" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "Starting Services" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Start Backend
Write-Host "[BACKEND] Starting on http://localhost:8000..." -ForegroundColor Cyan
$script:BackendJob = Start-Job -ScriptBlock {
    Set-Location $using:ScriptRoot\backend
    $env:PYTHONUNBUFFERED = "1"
    uv run --directory .. uvicorn backend.main:app --host 0.0.0.0 --port 8000 2>&1
}

Start-Sleep -Seconds 2

# Start Frontend  
Write-Host "[FRONTEND] Starting on http://localhost:8501..." -ForegroundColor Magenta
$script:FrontendJob = Start-Job -ScriptBlock {
    Set-Location $using:ScriptRoot\frontend
    uv run --directory .. streamlit run $using:ScriptRoot\frontend\app.py 2>&1
}

Start-Sleep -Seconds 2

# Start Teams Agent
Write-Host "[TEAMS] Starting on http://localhost:3978..." -ForegroundColor Yellow
$script:TeamsAgentJob = Start-Job -ScriptBlock {
    Set-Location $using:ScriptRoot\teams-agent
    $env:PYTHONUNBUFFERED = "1"
    uv run python -u app.py 2>&1
}

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "✓ Services Running" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "Backend:     http://localhost:8000" -ForegroundColor Cyan
Write-Host "API Docs:    http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "Frontend:    http://localhost:8501" -ForegroundColor Magenta
Write-Host "Teams Agent: http://localhost:3978/api/messages" -ForegroundColor Yellow
Write-Host ""
Write-Host "Press Ctrl+C to stop all services" -ForegroundColor Yellow
Write-Host ""
Write-Host "--- Service Logs ---" -ForegroundColor Gray
Write-Host ""

# Stream logs from both jobs
try {
    while ($true) {
        # Get backend output
        $backendOutput = Receive-Job -Job $script:BackendJob -ErrorAction SilentlyContinue
        if ($backendOutput) {
            $backendOutput | ForEach-Object {
                Write-Host "[BACKEND]  $_" -ForegroundColor Cyan
            }
        }
        
        # Get frontend output
        $frontendOutput = Receive-Job -Job $script:FrontendJob -ErrorAction SilentlyContinue
        if ($frontendOutput) {
            $frontendOutput | ForEach-Object {
                Write-Host "[FRONTEND] $_" -ForegroundColor Magenta
            }
        }
        
        # Get teams agent output
        $teamsOutput = Receive-Job -Job $script:TeamsAgentJob -ErrorAction SilentlyContinue
        if ($teamsOutput) {
            $teamsOutput | ForEach-Object {
                Write-Host "[TEAMS]    $_" -ForegroundColor Yellow
            }
        }
        
        # Check if jobs are still running
        $backendState = (Get-Job -Id $script:BackendJob.Id).State
        $frontendState = (Get-Job -Id $script:FrontendJob.Id).State
        $teamsState = (Get-Job -Id $script:TeamsAgentJob.Id).State
        
        if ($backendState -eq "Failed" -or $backendState -eq "Stopped") {
            Write-Host "`n[BACKEND] Service stopped unexpectedly!" -ForegroundColor Red
            Cleanup
        }
        
        if ($frontendState -eq "Failed" -or $frontendState -eq "Stopped") {
            Write-Host "`n[FRONTEND] Service stopped unexpectedly!" -ForegroundColor Red
            Cleanup
        }
        
        if ($teamsState -eq "Failed" -or $teamsState -eq "Stopped") {
            Write-Host "`n[TEAMS] Service stopped unexpectedly!" -ForegroundColor Red
            Cleanup
        }
        
        Start-Sleep -Milliseconds 500
    }
} catch {
    Cleanup
} finally {
    Cleanup
}
