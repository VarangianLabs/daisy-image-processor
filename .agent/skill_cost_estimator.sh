#!/usr/bin/env bash

# Enforce strict error handling principles (Bash fail-fast)
set -euo pipefail

# Systems Constants (Standard AWS x86_64 Lambda Pricing Models)
PRICE_PER_GB_SECOND=0.0000166667
PRICE_PER_MILLION_REQUESTS=0.20
TERRAFORM_MAIN="terraform/main.tf"

echo "====================================================="
echo "💰 SKILL ACTIVATED: Serverless Cost Estimator (.sh)"
echo "====================================================="

# 1. Inspect Infrastructure to Extract Provisioned Memory Allocation
DEFAULT_MEMORY=512
MEMORY_ALLOCATION=${DEFAULT_MEMORY}

if [ -f "${TERRAFORM_MAIN}" ]; then
    echo "🔍 Parsing ${TERRAFORM_MAIN} for memory configurations..."
    # Extracts the numerical value assigned to memory_size in the tf file
    EXTRACTED_MEM=$(grep -E 'memory_size\s*=' "${TERRAFORM_MAIN}" | grep -oE '[0-9]+' || true)
    if [ -n "${EXTRACTED_MEM}" ]; then
        MEMORY_ALLOCATION="${EXTRACTED_MEM}"
        echo "⚙️  Detected provisioned Lambda memory: ${MEMORY_ALLOCATION}MB"
    else
        echo "ℹ️  No custom memory_size declared. Assuming AWS Default: ${MEMORY_ALLOCATION}MB"
    fi
else
    echo "⚠️  ${TERRAFORM_MAIN} not found. Running calculations on baseline default: ${MEMORY_ALLOCATION}MB"
fi

# 2. Establish Simulated Processing Profiles for Image Transformations
# Profiling metrics: execution durations mapped to standard file manipulation sizes
echo "📊 Analyzing architectural cost profiles for 1,000,000 image actions..."
echo "-----------------------------------------------------"
printf "%-18s | %-12s | %-15s\n" "Image Profile" "Avg Duration" "Est. Cost / 1M"
echo "-----------------------------------------------------"

# Define profiling matrices [Profile_Name:Avg_Duration_In_Seconds]
PROFILES=(
    "Small-Avatar:0.150"
    "Medium-Res:0.450"
    "High-Res-4K:1.200"
)

for PROFILE in "${PROFILES[@]}"; do
    NAME="${PROFILE%%:*}"
    DURATION="${PROFILE##*:}"
    
    # Calculate compute GB-seconds: (Memory MB / 1024) * Duration * 1,000,000 Invocations
    # Compute Cost = GB-seconds * PRICE_PER_GB_SECOND
    # Request Cost = 1,000,000 * (PRICE_PER_MILLION_REQUESTS / 1,000,000)
    TOTAL_COST=$(awk -v mem="${MEMORY_ALLOCATION}" -v dur="${DURATION}" -v p_gb="${PRICE_PER_GB_SECOND}" -v p_req="${PRICE_PER_MILLION_REQUESTS}" '
        BEGIN {
            gb_seconds = (mem / 1024) * dur * 1000000;
            compute_cost = gb_seconds * p_gb;
            request_cost = p_req;
            printf "%.2f", (compute_cost + request_cost);
        }
    ')
    
    printf "%-18s | %-12s | \$%-15s\n" "${NAME}" "${DURATION}s" "${TOTAL_COST}"
done
echo "-----------------------------------------------------"

# 3. Micro-Optimization Architectural Guidance
echo "🧠 Structural Optimization Analysis:"
if [ "${MEMORY_ALLOCATION}" -lt 512 ]; then
    echo "⚠️  WARNING: Allocating under 512MB for Pillow processing may trigger excessive garbage collection"
    echo "   and CPU throttling, elongating runtimes and increasing net expenditure."
elif [ "${MEMORY_ALLOCATION}" -gt 1536 ]; then
    echo "💡 OPTIMIZATION TIP: High memory allocation observed. Monitor your real-world usage execution."
    echo "   If memory utilization sits below 30%, drop allocation to save on compute tier overheads."
else
    echo "✅ BALANCED: Current memory structure provides an optimized CPU-to-RAM ratio for image processing operations."
fi
echo "====================================================="