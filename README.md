# When Malicious Instructions Persist: Persistent Memory Poisoning Attack on Harness-Based Agents

## Basic Setup

### Agent runtimes

Download and install both agent runtimes locally:

- Install [Claude Code](https://docs.anthropic.com/en/docs/claude-code/getting-started).
- Install [OpenClaw](https://openclaw.ai/).

### Model APIs

We use `deepseek-v4-flash`, `deepseek-v4-pro`, and `qwen3-max` in our evaluations. Apply for API
access through the official platforms below:

- Create a key on the [DeepSeek API Platform](https://platform.deepseek.com/).
- Create a Qwen3 API key with [Alibaba Cloud Model Studio](https://modelstudio.alibabacloud.com/).


### Python environment

Python 3.10 or newer is required; Python 3.12 is recommended. Create and activate a virtual
environment, then install the project dependencies:

```bash
python -m venv .venv

# Windows Git Bash
source .venv/Scripts/activate

# Linux or macOS
# source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Evaluation workspace setup

After cloning this repository, run all commands from the repository root. Before evaluation, follow the
[`skills/README.md`](skills/README.md) instructions to install the skills, then copy the environment
and sample data into the OpenClaw state directory:

```bash
export OPENCLAW_STATE_DIR="/path/to/openclaw"

mkdir -p "$OPENCLAW_STATE_DIR/environment"
mkdir -p "$OPENCLAW_STATE_DIR/download"

cp -R environment/. "$OPENCLAW_STATE_DIR/environment/"
cp -R download/. "$OPENCLAW_STATE_DIR/download/"
```

Claude Code uses the repository's `environment/` and `download/` directories. OpenClaw uses the
copies under `OPENCLAW_STATE_DIR`.

## Evaluation

The batch scripts use `deepseek-v4-pro` as the default LLM. To use `deepseek-v4-flash` or
`qwen3-max`, update the corresponding Claude Code or OpenClaw configuration first, then change the
script's `MODEL_NAME` to the matching LLM identifier. You can also override it for a single run with
`MODEL_NAME=<model-name>`.

### Main Evaluation

Run the ISR evaluation across four scenarios and three input modalities:

```bash
OPENCLAW_STATE_DIR="/path/to/openclaw" \
bash jobscripts/batch/main_injection.sh
```

Run the C-ASR evaluation across four scenarios:

```bash
OPENCLAW_STATE_DIR="/path/to/openclaw" \
bash jobscripts/batch/main_trigger.sh
```

### Ablation Studies

Evaluate different injection positions and styles:

```bash
OPENCLAW_STATE_DIR="/path/to/openclaw" \
bash jobscripts/batch/ablation_strategies.sh
```

Evaluate different numbers of benign interactions before the trigger:

```bash
OPENCLAW_STATE_DIR="/path/to/openclaw" \
bash jobscripts/batch/ablation_multi_interactions.sh
```

### Defense Evaluation

Run the defended injection and trigger evaluations for the calendar scenario:

```bash
OPENCLAW_STATE_DIR="/path/to/openclaw" \
bash jobscripts/batch/defense.sh
```
