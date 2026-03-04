FROM python:3.12-slim

WORKDIR /app

COPY packages/schemas/python /app/packages/schemas/python
COPY apps/api /app/apps/api

RUN pip install --no-cache-dir -e /app/packages/schemas/python && \
    pip install --no-cache-dir -e /app/apps/api

CMD ["uvicorn", "ralphite_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
