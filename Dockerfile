FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3 numpy==2.2.6 scipy==1.13.1
COPY app.py index.html ./
RUN mkdir -p /data
ENV DB_PATH=/data/profili.db
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/profiles', timeout=4).status==200 else 1)"
CMD ["python", "app.py"]
