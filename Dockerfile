FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3 scipy==1.13.1
COPY app.py index.html ./
RUN mkdir -p /data
ENV DB_PATH=/data/profili.db
EXPOSE 5000
CMD ["python", "app.py"]