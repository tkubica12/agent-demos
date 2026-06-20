FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HERMES_HOME=/files/hermes
ENV HERMES_EXECUTABLE=hermes
ENV HERMES_TURN_TIMEOUT_SECONDS=900

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8088

CMD ["python", "main.py"]

