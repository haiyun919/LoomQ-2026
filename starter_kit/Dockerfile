FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOOMQ_SPINQ_PYTHON=/opt/loomq-spinq/bin/python

WORKDIR /workspace/submission
COPY requirements.txt requirements-spinq.txt ./

# Braket and SpinQit require incompatible antlr4 runtimes, so build two fully
# isolated interpreters. LOOMQ_SPINQ_PYTHON selects the SpinQ one at run time.
RUN python -m venv /opt/loomq-braket \
    && /opt/loomq-braket/bin/pip install --no-cache-dir -r requirements.txt \
    && python -m venv /opt/loomq-spinq \
    && /opt/loomq-spinq/bin/pip install --no-cache-dir -r requirements-spinq.txt

COPY . .

CMD ["/opt/loomq-braket/bin/python", "evaluator.py", "--level", "l1", "--target", "spinq,braket", "--json-out", "/tmp/loomq-public-report.json"]
