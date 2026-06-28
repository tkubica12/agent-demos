FROM python:3.12-slim

WORKDIR /app

COPY bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge ./bridge
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

ENTRYPOINT ["python", "-m", "uvicorn", "bridge.app:app", "--host", "0.0.0.0", "--port", "8000"]
