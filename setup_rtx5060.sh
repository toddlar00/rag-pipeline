#!/usr/bin/env bash
# setup_rtx5060.sh — Environment setup for CivPro RAG pipeline on RTX 5060 (8GB)
#
# RTX 5060 specs that matter:
#   - Blackwell architecture (GB206), compute capability sm_120
#   - 8GB GDDR7, 128-bit bus
#   - 3840 CUDA cores, 120 Tensor Cores (5th gen)
#   - TGP 145W
#
# The critical dependency chain:
#   NVIDIA driver 570+ → CUDA 12.8+ → PyTorch 2.7+ (cu128) → Docling
#
# PyTorch stable only added sm_120 support in 2.7.0 with cu128 wheels.
# Earlier stable releases (2.5, 2.6) will throw:
#   "CUDA error: no kernel image is available for execution on the device"

set -euo pipefail

echo "============================================"
echo "CivPro RAG Pipeline — RTX 5060 Setup"
echo "============================================"

# -------------------------------------------------------------------
# 1. Preflight checks
# -------------------------------------------------------------------
echo ""
echo "[1/6] Preflight checks..."

# Check NVIDIA driver
if ! command -v nvidia-smi &> /dev/null; then
    echo "ERROR: nvidia-smi not found. Install NVIDIA driver 570+ first."
    echo "  Ubuntu:  sudo apt install nvidia-driver-570"
    echo "  Fedora:  sudo dnf install akmod-nvidia"
    echo "  Or grab from: https://www.nvidia.com/Download/index.aspx"
    exit 1
fi

echo "  GPU detected:"
nvidia-smi --query-gpu=name,driver_version,compute_cap,memory.total --format=csv,noheader
echo ""

# Parse driver version
DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | tr -d ' ')
DRIVER_MAJOR=$(echo "$DRIVER_VER" | cut -d. -f1)

if [ "$DRIVER_MAJOR" -lt 570 ]; then
    echo "WARNING: Driver $DRIVER_VER detected. Blackwell requires 570+."
    echo "  Update before proceeding."
    exit 1
fi
echo "  Driver $DRIVER_VER — OK (≥570)"

# Check compute capability
COMPUTE_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
echo "  Compute capability: $COMPUTE_CAP"

if [[ "$COMPUTE_CAP" != "12.0" ]]; then
    echo "  NOTE: Expected sm_120 for RTX 5060. Got $COMPUTE_CAP."
    echo "  Pipeline will still work but batch sizes are tuned for 8GB VRAM."
fi

# -------------------------------------------------------------------
# 2. Python venv
# -------------------------------------------------------------------
echo ""
echo "[2/6] Creating Python virtual environment..."

# Python 3.12+ required for Blackwell PyTorch wheels
PYTHON_CMD=""
for cmd in python3.13 python3.12 python3; do
    if command -v "$cmd" &> /dev/null; then
        PY_VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MINOR=$("$cmd" -c "import sys; print(sys.version_info.minor)")
        if [ "$PY_MINOR" -ge 12 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: Python 3.12+ required for Blackwell cu128 wheels."
    echo "  Install: sudo apt install python3.12 python3.12-venv"
    exit 1
fi
echo "  Using $PYTHON_CMD ($PY_VER)"

$PYTHON_CMD -m venv .venv
source .venv/bin/activate

# -------------------------------------------------------------------
# 3. PyTorch with CUDA 12.8 (sm_120 support)
# -------------------------------------------------------------------
echo ""
echo "[3/6] Installing PyTorch 2.7+ with CUDA 12.8 (Blackwell support)..."

# THIS IS THE CRITICAL LINE — the --index-url is what pulls cu128 wheels
# instead of CPU-only. Without it, you get "no kernel image" errors.
pip install --upgrade pip
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

echo ""
echo "  Verifying CUDA detection..."
python -c "
import torch
print(f'  PyTorch version:  {torch.__version__}')
print(f'  CUDA available:   {torch.cuda.is_available()}')
print(f'  CUDA version:     {torch.version.cuda}')
if torch.cuda.is_available():
    print(f'  GPU name:         {torch.cuda.get_device_name(0)}')
    print(f'  Compute cap:      {torch.cuda.get_device_capability(0)}')
    vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f'  VRAM:             {vram:.1f} GB')
    # Smoke test — actually run something on the GPU
    x = torch.randn(100, 100, device='cuda')
    y = x @ x.T
    print(f'  Smoke test:       PASSED (matmul on CUDA)')
else:
    print('  ERROR: CUDA not available after install!')
    print('  Check: driver version, CUDA toolkit, and cu128 wheel')
    exit(1)
"

# -------------------------------------------------------------------
# 4. Docling + dependencies
# -------------------------------------------------------------------
echo ""
echo "[4/6] Installing Docling and chunking dependencies..."

pip install docling
pip install "docling-core[chunking]"

# ChromaDB for vector indexing
pip install "chromadb>=1.5.2"

# Quality-of-life
pip install tqdm rich

# -------------------------------------------------------------------
# 5. Verify Docling sees the GPU
# -------------------------------------------------------------------
echo ""
echo "[5/6] Verifying Docling GPU acceleration..."

python -c "
import torch
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions

if torch.cuda.is_available():
    acc = AcceleratorOptions(device=AcceleratorDevice.CUDA)
    print(f'  Docling AcceleratorDevice: CUDA')
    print(f'  GPU: {torch.cuda.get_device_name(0)}')

    # VRAM-aware batch size recommendation for 8GB
    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    if vram_gb <= 8:
        rec_batch = 8
        print(f'  VRAM tier: 8GB — layout_batch_size={rec_batch}')
        print(f'  (Conservative to avoid OOM on 942-page doc)')
    elif vram_gb <= 12:
        rec_batch = 16
        print(f'  VRAM tier: 12GB — layout_batch_size={rec_batch}')
    else:
        rec_batch = 64
        print(f'  VRAM tier: {vram_gb:.0f}GB — layout_batch_size={rec_batch}')
else:
    print('  WARNING: Falling back to CPU mode')
    print('  Conversion will take ~30-45 min instead of ~5 min')
"

# -------------------------------------------------------------------
# 6. Summary
# -------------------------------------------------------------------
echo ""
echo "============================================"
echo "Setup complete!"
echo "============================================"
echo ""
echo "Quick start:"
echo "  source .venv/bin/activate"
echo "  python civpro_pipeline.py full --pdf /path/to/Civil_Procedure_9e.pdf"
echo ""
echo "Expected performance on RTX 5060 (8GB):"
echo "  Step 1 (convert):  ~5-8 min  (GPU layout model, batch_size=8)"
echo "  Step 2 (chunk):    ~30 sec   (CPU, token counting)"
echo "  Step 3 (index):    ~60 sec   (ChromaDB default embeddings)"
echo ""
echo "If you hit OOM during conversion, reduce layout_batch_size"
echo "in civpro_pipeline.py from 8 → 4."
echo ""
