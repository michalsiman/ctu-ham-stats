FROM python:3.12-slim

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# scripts/ obsahuje qrz_poc (paced sweep okresů) – app.okres ho importuje za běhu
# (denní job + POST /api/okres). Bez něj by okres úloha spadla na ModuleNotFoundError.
COPY scripts ./scripts
COPY config.ini ./config.ini

ENV DATA_DIR=/srv/data
VOLUME /srv/data
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
