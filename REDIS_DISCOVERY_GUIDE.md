# Redis SKU Discovery Tool - Minimal Permissions

A lightweight script to discover Azure Redis instances (OSS, Enterprise, and Managed Redis) across subscriptions with **minimal read-only permissions** and **graceful error handling** for partial access.

## Key Features

✅ Works with partial tenant access - scans only what you can access  
✅ Minimal permissions - no metrics/monitoring access required  
✅ Fast execution - no performance data collection  
✅ Multiple output formats - Excel, CSV, or JSON  
✅ Graceful error handling - continues on permission errors  
✅ **Resource calculations** - Includes vCPU and memory with HA awareness

## Output Information

The tool provides comprehensive inventory data including:

- **Instance details**: Name, region, resource group, SKU
- **Configuration**: Clustering, shard count, Redis version
- **High Availability**: Enabled/Disabled status
- **Resource allocation**: 
  - vCPU and memory **per shard/node**
  - **Total** vCPU and memory **(all nodes including replicas)**
- **Provisioning state** and resource IDs

💡 **Resource calculation notes:**
- **"Per Shard" columns**: Resources for a single shard/node
- **"Total (all nodes)" columns**: Sum across ALL nodes including replicas
- **Why count replicas?** Replicas run on separate VMs and consume:
  - Subscription quota
  - Billing costs
  - Infrastructure capacity
- **HA doubling**: When HA is enabled, total = per-node × 2 (primary + replica)

## Supported Redis Types

- **Azure Cache for Redis (OSS)**: Basic, Standard, Premium
- **Azure Cache for Redis Enterprise**: E1-E400
- **Azure Managed Redis (AMR)**: All SKU families (Balanced, Memory/Compute/Flash-Optimized)

## Required Permissions

**Per subscription you want to scan** (not all subscriptions!):

```
Microsoft.Cache/redis/read
Microsoft.Cache/redisEnterprise/read
Microsoft.Cache/redisenterprise/redisInstances/read
```

**Built-in role that works:** `Reader` (at subscription level)

**What's NOT required:**
- ❌ Tenant-wide permissions
- ❌ Monitoring Reader role
- ❌ Metrics access
- ❌ Access to ALL subscriptions

## Quick Start

### Installation

```bash
# Clone and setup
git clone https://github.com/Redislabs-Solution-Architects/acrp2acre.git
cd acrp2acre
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Authenticate
az login

# Run discovery
python discoverRedisSKUs.py -v
```

### Usage Examples

```bash
# Scan all accessible subscriptions
python discoverRedisSKUs.py

# Scan specific subscriptions
python discoverRedisSKUs.py -s "sub-id-1,sub-id-2"

# Verbose output with custom path
python discoverRedisSKUs.py -v -o /path/to/inventory.xlsx

# JSON output for automation
python discoverRedisSKUs.py -f json -o inventory.json

# Exclude Azure Managed Redis
```bash
python discoverRedisSKUs.py --exclude-amr
```

## Comparison: Discovery vs Metrics Script

| Feature | discoverRedisSKUs.py (NEW) | pullAzureCacheForRedisStats.py |
|---------|---------------------------|--------------------------------|
| **Purpose** | Quick inventory | Detailed performance analysis |
| **Permissions** | Read only | Read + Monitoring |
| **Speed** | Fast | Slow (90-day metrics) |
| **Partial Access** | ✅ Supported | ❌ Fails on errors |
| **Use Case** | Discovery, compliance | Sizing, migration planning |

**When to use which:**
1. **Discovery script** (this one) → Initial inventory, limited permissions, quick scan
2. **Metrics script** → Detailed sizing for migration, performance analysis

## Known Limitations

### Redis Version Detection
- **OSS Redis (Basic/Standard/Premium)**: ✅ Redis version detected (e.g., "6.0.14")
- **Enterprise E-series**: ⚠️ Redis version **not available** via Azure API
- **Azure Managed Redis (AMR)**: ✅ Redis version detected (e.g., "7.4") using preview API

The Azure Management API does not expose the Redis version for Enterprise E-series instances. For AMR instances, the script uses a preview API version (`2024-09-01-preview`) to retrieve the Redis version from the database properties.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No subscriptions found" | Run `az login`, or provide IDs with `-s` |
| "Permission denied" | Need Reader role on target subscriptions |
| Empty results | Verify correct tenant: `az account show` |
| Import errors | Activate venv: `source .venv/bin/activate` |

## Exit Codes

- `0` - Success (all subscriptions scanned)
- `1` - Error (authentication failed, no subscriptions)
- `2` - Partial success (some subscriptions failed)

## Support

Not officially supported. For issues:
- Check troubleshooting section
- Open GitHub issue with error details
- Verify `az account show` shows correct tenant

---
