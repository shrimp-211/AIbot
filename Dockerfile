FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir aiohttp pyyaml loguru pydantic httpx watchfiles

COPY . .

EXPOSE 6185 6199 6186
VOLUME ["/app/data"]

CMD ["python", "main.py"]
