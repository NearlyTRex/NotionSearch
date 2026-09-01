FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NOTIONSEARCH_DATA=/data

WORKDIR /srv

# gosu drops privileges in the entrypoint after fixing up /data ownership.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so code edits don't invalidate the layer.
COPY requirements.txt .
# pip is a build-time tool: nothing at runtime uses it. Leaving it behind ships
# its vendored dependency tree (msgpack, setuptools and friends, listed in
# pip/_vendor/vendor.txt), which vulnerability scanners correctly report against
# the image even though that code never executes here. Removing it deletes the
# vulnerable files rather than suppressing the finding.
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /usr/local/lib/python3.*/site-packages/pip \
              /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
              /usr/local/lib/python3.*/ensurepip \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

COPY app/ ./app/
COPY web/ ./web/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health',timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
