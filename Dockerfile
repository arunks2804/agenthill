# --- build the React frontend ---
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --silent
COPY frontend/ ./
RUN npx vite build

# --- Python backend serving API + built frontend ---
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY data/ data/
COPY scripts/ scripts/
COPY --from=web /web/dist frontend/dist
ENV AGENTHILL_VAR_DIR=/app/var \
    OLLAMA_BASE_URL=http://host.docker.internal:11434
EXPOSE 8000
CMD ["sh", "-c", "python scripts/prepare_demo.py --if-empty && uvicorn --app-dir backend agenthill.main:app --host 0.0.0.0 --port 8000"]
