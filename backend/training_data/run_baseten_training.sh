#!/bin/bash
set -eux

python -m pip install \
  "accelerate==1.15.0" \
  "datasets==5.0.1" \
  "peft==0.21.0" \
  "transformers==5.17.0" \
  "trl==1.13.0"
python baseten_train.py
