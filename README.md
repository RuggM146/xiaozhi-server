# XiaoZhi Server (Python Backend)

Open-source Python WebSocket and HTTP backend for XiaoZhi AI voice assistant (ESP32-S3).

## Features
- **Low-latency Voice Processing**: Native Opus compressed audio streaming over WebSocket.
- **LLM**: Integrated with Qwen 2.5 (via SiliconFlow or OpenAI-compatible endpoints) and Google Gemini.
- **TTS**: High-fidelity neural voice synthesis with CosyVoice 2 (SiliconFlow) and Edge-TTS.
- **ASR & VAD**: SileroVAD voice activity detection with SenseVoice/FunASR speech recognition.
- **MCP Support**: Model Context Protocol integration for extensible IoT actions and function calling.

## Configuration
Edit `data/.config.yaml` or set environment variables to configure your API keys (SiliconFlow, DashScope, Gemini).

## Quick Start
```bash
pip install -r requirements.txt
python app.py
```
