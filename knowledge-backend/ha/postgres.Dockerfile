# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --python /usr/local/bin/python3 \
    --target /opt/patroni 'patroni[etcd3,psycopg3]==4.1.3' 'psycopg[binary]==3.2.13'

FROM pgvector/pgvector:pg16
COPY --from=runtime /usr/local/ /usr/local/
COPY --from=runtime /opt/patroni/ /opt/patroni/
COPY patroni.yml /etc/patroni.yml
ENV PYTHONPATH=/opt/patroni PATH=/usr/lib/postgresql/16/bin:$PATH
RUN ldconfig && install -d -o postgres -g postgres -m 0700 /var/lib/postgresql/patroni
USER postgres
ENTRYPOINT ["python3", "-m", "patroni", "/etc/patroni.yml"]
