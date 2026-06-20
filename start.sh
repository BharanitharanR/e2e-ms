#!/bin/bash

ollama serve &

echo "Waiting for Ollama..."

sleep 10

echo "Pulling qwen3:8b..."

ollama pull qwen3:8b

echo "Model ready"

wait