FROM python:3.11-slim
WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-databento.txt ./
# los deploys llevan todo: el peso extra no molesta en un servidor
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r requirements-databento.txt
COPY backend /app/backend
COPY frontend /app/frontend
EXPOSE 8080
# Railway/Render/Heroku inject PORT; default to 8080 elsewhere (Fly)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
