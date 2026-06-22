#!/bin/bash

set -e

MODEL_NAME=${OLLAMA_MODEL:-qwen3:8b}

echo "Starting Ollama..."

ollama serve &

OLLAMA_PID=$!

echo "Waiting for Ollama API..."

until ollama list >/dev/null 2>&1
do
    sleep 2
done

echo "Checking model: $MODEL_NAME"

if ! ollama list | grep -q "$MODEL_NAME"; then
    echo "Downloading $MODEL_NAME ..."
    ollama pull "$MODEL_NAME"
else
    echo "$MODEL_NAME already exists"
fi

echo "Ollama ready"

wait $OLLAMA_PID