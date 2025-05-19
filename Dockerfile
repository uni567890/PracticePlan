FROM python:3.9-slim-buster

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y libpq-dev
RUN pip install psycopg2-binary -r requirements.txt

COPY . .

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]