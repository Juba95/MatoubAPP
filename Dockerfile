FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/villes_france.csv ./villes_france.csv
COPY frontend/dashboard.html ./static/dashboard.html

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
# force rebuild lun. 13 avr. 2026 00:57:28 CEST
