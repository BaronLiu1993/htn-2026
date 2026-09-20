"""Baseten Training Jobs configuration for the underwriting Qwen3-8B LoRA run."""

from truss.base.truss_config import AcceleratorSpec
from truss_train import CacheConfig, CheckpointingConfig, Compute, Image, Runtime, TrainingJob, TrainingProject


BASE_IMAGE = "pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime"

training_runtime = Runtime(
    start_commands=["chmod +x ./run_baseten_training.sh && ./run_baseten_training.sh"],
    cache_config=CacheConfig(enabled=True),
    checkpointing_config=CheckpointingConfig(enabled=True),
)

training_compute = Compute(
    accelerator=AcceleratorSpec(accelerator="H100", count=1),
    node_count=1,
)

training_job = TrainingJob(
    image=Image(base_image=BASE_IMAGE),
    compute=training_compute,
    runtime=training_runtime,
)

training_project = TrainingProject(
    # Project names must be unique within a Baseten team. The previous H100
    # project contains the failed warmup_ratio code snapshot, so use v2.
    name="underwriting-qwen3-8b-lora-h100-v2",
    job=training_job,
)
