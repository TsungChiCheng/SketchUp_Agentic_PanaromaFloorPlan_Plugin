ARG PYTORCH_IMAGE=pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
FROM ${PYTORCH_IMAGE}

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV DEPTH_MODEL=depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf
ENV HF_HOME=/opt/huggingface

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN python -c "from transformers import AutoImageProcessor, AutoModelForDepthEstimation; import os; model=os.environ['DEPTH_MODEL']; AutoImageProcessor.from_pretrained(model, use_fast=False); AutoModelForDepthEstimation.from_pretrained(model)"

COPY . .

RUN mkdir -p /opt/huggingface && useradd --create-home --shell /bin/bash app && chown -R app:app /app /opt/huggingface
USER app

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
