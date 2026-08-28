. "$PSScriptRoot\common.ps1"
$outputs = Get-BootstrapOutput
$botId = Get-OutputValue $outputs "bot_id"
$targets = @(
    ("Direct" + "LineChannel"),
    ("Web" + "ChatChannel")
)
$channelsUrl = "https://management.azure.com${botId}/channels?api-version=2022-09-15"
$channels = az rest --method GET --url $channelsUrl --query "value[].name" -o tsv
foreach ($target in $targets) {
    $qualified = "$(Get-OutputValue $outputs 'bot_name')/$target"
    if ($channels -contains $qualified) {
        $channelUrl = "https://management.azure.com${botId}/channels/${target}?api-version=2022-09-15"
        Invoke-Native az rest --method DELETE --url $channelUrl --only-show-errors
    }
}
