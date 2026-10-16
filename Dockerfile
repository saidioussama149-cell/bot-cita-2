FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_cloud_directo.py .

CMD ["python", "-u", "bot_cloud_directo.py"]
