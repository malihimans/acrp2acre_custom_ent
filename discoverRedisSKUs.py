#!/usr/bin/env python3
"""
Azure Redis SKU Discovery Script

This script discovers Azure Cache for Redis instances across accessible subscriptions
with minimal permission requirements and graceful error handling.

Supports:
- Azure Cache for Redis (Basic, Standard, Premium)
- Azure Cache for Redis Enterprise
- Azure Managed Redis (AMR)

Key Features:
- Works with partial tenant access
- Graceful error handling for permission issues
- No metrics collection required
- Fast execution
"""

from azure.identity import DefaultAzureCredential, AzureCliCredential
from azure.mgmt.redis import RedisManagementClient
from azure.mgmt.redisenterprise import RedisEnterpriseManagementClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.subscription import SubscriptionClient
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
import pandas as pd
from pathlib import Path
import argparse
import sys
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import json

# SKU Resource Mappings (vCPU and Memory per node)
# Source: https://learn.microsoft.com/en-us/azure/azure-cache-for-redis/

# OSS Redis (Basic/Standard/Premium) - Memory in GB
OSS_REDIS_RESOURCES = {
    # Basic tier (no HA)
    'C0': {'vcpu': 0.5, 'memory_gb': 0.25},
    'C1': {'vcpu': 1, 'memory_gb': 1},
    'C2': {'vcpu': 2, 'memory_gb': 2.5},
    'C3': {'vcpu': 4, 'memory_gb': 6},
    'C4': {'vcpu': 2, 'memory_gb': 13},
    'C5': {'vcpu': 4, 'memory_gb': 26},
    'C6': {'vcpu': 8, 'memory_gb': 53},
    # Premium tier
    'P1': {'vcpu': 2, 'memory_gb': 6},
    'P2': {'vcpu': 4, 'memory_gb': 13},
    'P3': {'vcpu': 4, 'memory_gb': 26},
    'P4': {'vcpu': 8, 'memory_gb': 53},
    'P5': {'vcpu': 16, 'memory_gb': 120},
}

# Enterprise E-series - per capacity unit
ENTERPRISE_RESOURCES = {
    'Enterprise_E1': {'vcpu': 2, 'memory_gb': 12},
    'Enterprise_E5': {'vcpu': 4, 'memory_gb': 25},
    'Enterprise_E10': {'vcpu': 8, 'memory_gb': 50},
    'Enterprise_E20': {'vcpu': 16, 'memory_gb': 100},
    'Enterprise_E50': {'vcpu': 32, 'memory_gb': 250},
    'Enterprise_E100': {'vcpu': 64, 'memory_gb': 500},
    'Enterprise_E200': {'vcpu': 128, 'memory_gb': 1000},
    'Enterprise_E400': {'vcpu': 256, 'memory_gb': 2000},
}

# Azure Managed Redis (AMR) - per node
AMR_RESOURCES = {
    # Balanced tier
    'Balanced_B0': {'vcpu': 2, 'memory_gb': 1},
    'Balanced_B1': {'vcpu': 2, 'memory_gb': 3},
    'Balanced_B3': {'vcpu': 4, 'memory_gb': 9},
    'Balanced_B5': {'vcpu': 8, 'memory_gb': 15},
    'Balanced_B10': {'vcpu': 8, 'memory_gb': 30},
    'Balanced_B20': {'vcpu': 16, 'memory_gb': 60},
    'Balanced_B50': {'vcpu': 32, 'memory_gb': 150},
    'Balanced_B100': {'vcpu': 64, 'memory_gb': 300},
    'Balanced_B150': {'vcpu': 96, 'memory_gb': 450},
    'Balanced_B250': {'vcpu': 128, 'memory_gb': 750},
    'Balanced_B350': {'vcpu': 192, 'memory_gb': 1050},
    'Balanced_B500': {'vcpu': 256, 'memory_gb': 1500},
    'Balanced_B700': {'vcpu': 320, 'memory_gb': 2100},
    'Balanced_B1000': {'vcpu': 448, 'memory_gb': 3000},
    # Memory Optimized tier
    'MemoryOptimized_M10': {'vcpu': 8, 'memory_gb': 30},
    'MemoryOptimized_M20': {'vcpu': 16, 'memory_gb': 60},
    'MemoryOptimized_M50': {'vcpu': 32, 'memory_gb': 150},
    'MemoryOptimized_M100': {'vcpu': 64, 'memory_gb': 300},
    'MemoryOptimized_M150': {'vcpu': 96, 'memory_gb': 450},
    'MemoryOptimized_M250': {'vcpu': 128, 'memory_gb': 750},
    'MemoryOptimized_M350': {'vcpu': 192, 'memory_gb': 1050},
    'MemoryOptimized_M500': {'vcpu': 256, 'memory_gb': 1500},
    'MemoryOptimized_M700': {'vcpu': 320, 'memory_gb': 2100},
    'MemoryOptimized_M1000': {'vcpu': 448, 'memory_gb': 3000},
    'MemoryOptimized_M1500': {'vcpu': 640, 'memory_gb': 4500},
    'MemoryOptimized_M2000': {'vcpu': 896, 'memory_gb': 6000},
    # Compute Optimized tier
    'ComputeOptimized_X3': {'vcpu': 4, 'memory_gb': 9},
    'ComputeOptimized_X5': {'vcpu': 8, 'memory_gb': 15},
    'ComputeOptimized_X10': {'vcpu': 8, 'memory_gb': 30},
    'ComputeOptimized_X20': {'vcpu': 16, 'memory_gb': 60},
    'ComputeOptimized_X50': {'vcpu': 32, 'memory_gb': 150},
    'ComputeOptimized_X100': {'vcpu': 64, 'memory_gb': 300},
    'ComputeOptimized_X150': {'vcpu': 96, 'memory_gb': 450},
    'ComputeOptimized_X250': {'vcpu': 128, 'memory_gb': 750},
    'ComputeOptimized_X350': {'vcpu': 192, 'memory_gb': 1050},
    'ComputeOptimized_X500': {'vcpu': 256, 'memory_gb': 1500},
    'ComputeOptimized_X700': {'vcpu': 320, 'memory_gb': 2100},
    # Flash Optimized tier
    'FlashOptimized_A250': {'vcpu': 32, 'memory_gb': 250},
    'FlashOptimized_A500': {'vcpu': 64, 'memory_gb': 500},
    'FlashOptimized_A700': {'vcpu': 96, 'memory_gb': 700},
    'FlashOptimized_A1000': {'vcpu': 128, 'memory_gb': 1000},
    'FlashOptimized_A1500': {'vcpu': 192, 'memory_gb': 1500},
    'FlashOptimized_A2000': {'vcpu': 256, 'memory_gb': 2000},
    'FlashOptimized_A4500': {'vcpu': 512, 'memory_gb': 4500},
    # General Purpose tier (older)
    'GeneralPurpose_G3': {'vcpu': 4, 'memory_gb': 9},
    'GeneralPurpose_G5': {'vcpu': 8, 'memory_gb': 15},
}



class SubscriptionScanResult:
    """Result of scanning a single subscription"""
    def __init__(self, subscription_id: str, subscription_name: str = ""):
        self.subscription_id = subscription_id
        self.subscription_name = subscription_name
        self.success = False
        self.error_type = None
        self.error_message = None
        self.redis_instances = []
        
    def mark_success(self, instances: list):
        self.success = True
        self.redis_instances = instances
        
    def mark_failure(self, error_type: str, error_message: str):
        self.success = False
        self.error_type = error_type
        self.error_message = error_message


class RedisInstance:
    """Represents a discovered Redis instance"""
    def __init__(self, subscription_id: str, subscription_name: str):
        self.subscription_id = subscription_id
        self.subscription_name = subscription_name
        self.resource_group = ""
        self.name = ""
        self.location = ""
        self.redis_type = ""  # "OSS", "Enterprise", "Managed"
        self.sku_family = ""
        self.sku_name = ""
        self.sku_capacity = ""
        self.clustering_enabled = "No"
        self.shard_count = 0
        self.provisioning_state = ""
        self.redis_version = ""
        self.resource_id = ""
        self.high_availability = ""  # "Enabled", "Disabled", "N/A"
        self.vcpu_per_node = 0
        self.memory_gb_per_node = 0
        self.total_vcpu = 0
        self.total_memory_gb = 0
        
    def to_dict(self) -> dict:
        return {
            'Subscription ID': self.subscription_id,
            'Subscription Name': self.subscription_name,
            'Resource Group': self.resource_group,
            'Instance Name': self.name,
            'Region': self.location,
            'Redis Type': self.redis_type,
            'SKU Name': self.sku_name,
            'Redis Version': self.redis_version,
            'High Availability': self.high_availability,
            'vCPU per Shard': self.vcpu_per_node,
            'Memory GB per Shard': self.memory_gb_per_node,
            'Total vCPU (all nodes)': self.total_vcpu,
            'Total Memory GB (all nodes)': self.total_memory_gb,
            'Provisioning State': self.provisioning_state,
            'Clustering Enabled': self.clustering_enabled,
            'Shard Count': self.shard_count,
            'SKU Family': self.sku_family,
            'Capacity': self.sku_capacity,
            'Resource ID': self.resource_id
        }


def get_resource_group_from_id(resource_id: str) -> str:
    """Extract resource group name from Azure resource ID"""
    try:
        parts = resource_id.split('/')
        rg_index = parts.index('resourceGroups') + 1
        return parts[rg_index]
    except (ValueError, IndexError):
        return ""


def calculate_oss_resources(instance: RedisInstance, sku_family: str, sku_capacity: str, sku_name: str):
    """
    Calculate vCPU and memory for OSS Redis instances.
    
    Architecture notes:
    - Basic tier: Single node (no HA)
    - Standard/Premium: Primary + Replica on SEPARATE hosts
    
    Important: Replicas are on separate VMs, so:
    - Customer pays for both nodes
    - Both nodes consume subscription quota
    - Total resources = resources needed for the deployment
    
    For migration/sizing purposes, we report total resources across all nodes.
    """
    # Build the SKU key (e.g., "C0", "P1")
    sku_key = f"{sku_family}{sku_capacity}"
    
    if sku_key in OSS_REDIS_RESOURCES:
        resources = OSS_REDIS_RESOURCES[sku_key]
        instance.vcpu_per_node = resources['vcpu']
        instance.memory_gb_per_node = resources['memory_gb']
        
        # Basic tier has no HA (single node), Standard/Premium have replicas on separate hosts
        if sku_name == 'Basic':
            instance.high_availability = "Disabled"
            instance.total_vcpu = instance.vcpu_per_node
            instance.total_memory_gb = instance.memory_gb_per_node
        else:
            # Standard and Premium have 1 replica on a separate host (2 nodes total)
            # Total = Primary node + Replica node resources
            instance.high_availability = "Enabled"
            instance.total_vcpu = instance.vcpu_per_node * 2
            instance.total_memory_gb = instance.memory_gb_per_node * 2


def calculate_enterprise_amr_resources(instance: RedisInstance, sku_name: str, high_availability: str):
    """
    Calculate vCPU and memory for Enterprise and AMR instances.
    
    Architecture notes:
    - Enterprise/AMR with HA: Primary + Replica on SEPARATE nodes
    - Enterprise/AMR without HA: Single node
    
    Similar to OSS Standard/Premium, when HA is enabled:
    - Replicas are on separate infrastructure
    - Customer pays for both primary and replica
    - Both consume subscription quota
    
    Total resources = sum across all nodes in the deployment.
    """
    # Determine which mapping to use
    if sku_name in ENTERPRISE_RESOURCES:
        resources = ENTERPRISE_RESOURCES[sku_name]
    elif sku_name in AMR_RESOURCES:
        resources = AMR_RESOURCES[sku_name]
    else:
        # Unknown SKU
        return
    
    instance.vcpu_per_node = resources['vcpu']
    instance.memory_gb_per_node = resources['memory_gb']
    instance.high_availability = high_availability
    
    # If HA is enabled, resources are doubled (primary + replica on separate nodes)
    if high_availability == "Enabled":
        instance.total_vcpu = instance.vcpu_per_node * 2
        instance.total_memory_gb = instance.memory_gb_per_node * 2
    else:
        instance.total_vcpu = instance.vcpu_per_node
        instance.total_memory_gb = instance.memory_gb_per_node


def parse_oss_redis_instance(cluster, subscription_id: str, subscription_name: str) -> RedisInstance:
    """Parse Azure Cache for Redis (OSS) instance"""
    instance = RedisInstance(subscription_id, subscription_name)
    instance.resource_group = get_resource_group_from_id(cluster.id)
    instance.name = cluster.name
    instance.location = cluster.location
    instance.redis_type = "OSS"
    instance.sku_family = cluster.sku.family
    instance.sku_name = cluster.sku.name
    instance.sku_capacity = str(cluster.sku.capacity)
    instance.shard_count = cluster.shard_count or 1
    instance.clustering_enabled = "Yes" if cluster.shard_count and cluster.shard_count > 1 else "No"
    instance.provisioning_state = cluster.provisioning_state or ""
    instance.redis_version = cluster.redis_version or ""
    instance.resource_id = cluster.id
    
    # Calculate vCPU and memory
    calculate_oss_resources(instance, cluster.sku.family, str(cluster.sku.capacity), cluster.sku.name)
    
    return instance


def parse_enterprise_redis_instance(cluster, subscription_id: str, subscription_name: str, enterprise_client=None, credential=None) -> RedisInstance:
    """Parse Azure Cache for Redis Enterprise instance"""
    instance = RedisInstance(subscription_id, subscription_name)
    instance.resource_group = get_resource_group_from_id(cluster.id)
    instance.name = cluster.name
    instance.location = cluster.location
    instance.redis_type = "Enterprise"
    instance.sku_family = cluster.sku.name.split('_')[0] if '_' in cluster.sku.name else cluster.sku.name
    instance.sku_name = cluster.sku.name
    instance.sku_capacity = str(cluster.sku.capacity) if cluster.sku.capacity else "0"
    
    # Check database-level clustering policy and get Redis version
    instance.clustering_enabled = "No"
    instance.shard_count = 1
    redis_version_from_db = None
    database_id = None
    
    if enterprise_client:
        try:
            rg = instance.resource_group
            databases = list(enterprise_client.databases.list_by_cluster(rg, cluster.name))
            if databases:
                # Check first database for clustering
                db = databases[0]
                database_id = db.id  # Save for later Redis version query
                clustering_policy = getattr(db, 'clustering_policy', None)
                if clustering_policy and clustering_policy in ['OSSCluster', 'EnterpriseCluster']:
                    instance.clustering_enabled = "Yes"
                    # EnterpriseCluster can have more shards
                    instance.shard_count = 1  # Would need to query further for exact count
                
                # Try to get Redis version from database using standard SDK
                redis_version_from_db = getattr(db, 'redis_version', None)
        except (HttpResponseError, ResourceNotFoundError):
            # Permission denied or database not found - expected in some scenarios
            # Fall back to capacity-based estimation below
            pass
        except Exception:
            # Unexpected error (API changes, network issues, etc.)
            # Fall back to capacity-based estimation below
            pass
    
    # Fallback: rough estimate based on capacity
    if instance.clustering_enabled == "No" and cluster.sku.capacity and cluster.sku.capacity > 2:
        instance.clustering_enabled = "Possibly"
    
    instance.provisioning_state = cluster.provisioning_state or ""
    
    # Get Redis version
    cluster_redis_version = getattr(cluster, 'redis_version', None)
    
    if cluster_redis_version:
        # OSS Redis or older Enterprise instances that have version info
        instance.redis_version = cluster_redis_version
    elif redis_version_from_db:
        # Database-level version from standard SDK
        instance.redis_version = redis_version_from_db
    elif credential and database_id:
        # For AMR instances, use newer API version to get Redis version and HA status
        try:
            resource_client = ResourceManagementClient(credential, subscription_id)
            # Use preview API version that exposes redisVersion property
            database_resource = resource_client.resources.get_by_id(database_id, '2024-09-01-preview')
            if database_resource.properties and 'redisVersion' in database_resource.properties:
                instance.redis_version = database_resource.properties['redisVersion']
            else:
                instance.redis_version = ''
        except Exception:
            # If newer API fails, fall back to empty string
            instance.redis_version = ''
    else:
        # No version available
        instance.redis_version = ''
    
    # Get HA status from cluster using Resource API
    high_availability = "Unknown"
    if credential:
        try:
            resource_client = ResourceManagementClient(credential, subscription_id)
            cluster_resource = resource_client.resources.get_by_id(cluster.id, '2024-09-01-preview')
            if cluster_resource.properties and 'highAvailability' in cluster_resource.properties:
                ha_value = cluster_resource.properties['highAvailability']
                high_availability = "Enabled" if ha_value == "Enabled" else "Disabled"
        except Exception:
            # If we can't get HA status, assume it's enabled for Enterprise/AMR
            high_availability = "Enabled"
    
    # Calculate vCPU and memory
    calculate_enterprise_amr_resources(instance, instance.sku_name, high_availability)
    
    instance.resource_id = cluster.id
    return instance


def is_amr_sku(sku_name: str) -> bool:
    """Check if SKU is Azure Managed Redis (AMR)"""
    amr_prefixes = ['GeneralPurpose_', 'Balanced_', 'MemoryOptimized_', 
                    'ComputeOptimized_', 'FlashOptimized_']
    return any(sku_name.startswith(prefix) for prefix in amr_prefixes)


def scan_subscription(credential, subscription_id: str, subscription_name: str, 
                     include_amr: bool, verbose: bool) -> SubscriptionScanResult:
    """Scan a single subscription for Redis instances"""
    result = SubscriptionScanResult(subscription_id, subscription_name)
    instances = []
    
    try:
        # Scan OSS Redis instances
        if verbose:
            print(f"    Scanning Azure Cache for Redis (OSS)...", end="", flush=True)
        
        redis_client = RedisManagementClient(credential, subscription_id)
        # Try both API methods for compatibility
        try:
            oss_clusters = list(redis_client.redis.list())
        except AttributeError:
            # Newer API version uses list_by_subscription
            oss_clusters = list(redis_client.redis.list_by_subscription())
        
        for cluster in oss_clusters:
            instances.append(parse_oss_redis_instance(cluster, subscription_id, subscription_name))
        
        if verbose:
            print(f" found {len(oss_clusters)}")
        
        # Scan Enterprise Redis instances
        if verbose:
            print(f"    Scanning Azure Cache for Redis Enterprise...", end="", flush=True)
        
        enterprise_client = RedisEnterpriseManagementClient(credential, subscription_id)
        # Try both API methods for compatibility
        try:
            enterprise_clusters = list(enterprise_client.redis_enterprise.list())
        except AttributeError:
            # Newer API version uses list_by_subscription
            enterprise_clusters = list(enterprise_client.redis_enterprise.list_by_subscription())
        
        # Filter AMR instances if needed
        for cluster in enterprise_clusters:
            if include_amr or not is_amr_sku(cluster.sku.name):
                instance = parse_enterprise_redis_instance(cluster, subscription_id, subscription_name, enterprise_client, credential)
                if is_amr_sku(cluster.sku.name):
                    instance.redis_type = "Managed"
                instances.append(instance)
        
        if verbose:
            print(f" found {len([c for c in enterprise_clusters if include_amr or not is_amr_sku(c.sku.name)])}")
        
        result.mark_success(instances)
        
    except HttpResponseError as e:
        if e.status_code == 403:
            result.mark_failure("Permission Denied", "Insufficient permissions to read Redis resources")
        elif e.status_code == 404:
            result.mark_failure("Not Found", "Subscription not found or not accessible")
        else:
            result.mark_failure("HTTP Error", f"HTTP {e.status_code}: {str(e)}")
    except ResourceNotFoundError:
        result.mark_failure("Not Found", "Subscription not found")
    except Exception as e:
        result.mark_failure("Other Error", str(e))
    
    return result


def get_accessible_subscriptions(credential, subscription_ids: Optional[List[str]], 
                                verbose: bool) -> List[Tuple[str, str]]:
    """Get list of accessible subscriptions"""
    subscriptions = []
    
    try:
        subscription_client = SubscriptionClient(credential)
        
        if subscription_ids:
            # Use provided subscription IDs
            if verbose:
                print(f"Using provided subscription IDs: {len(subscription_ids)}")
            for sub_id in subscription_ids:
                try:
                    sub = subscription_client.subscriptions.get(sub_id)
                    subscriptions.append((sub.subscription_id, sub.display_name))
                except Exception as e:
                    if verbose:
                        print(f"  Warning: Could not access subscription {sub_id}: {str(e)}")
                    subscriptions.append((sub_id, "Unknown"))
        else:
            # List all accessible subscriptions
            if verbose:
                print("Enumerating accessible subscriptions...")
            all_subs = list(subscription_client.subscriptions.list())
            subscriptions = [(sub.subscription_id, sub.display_name) for sub in all_subs]
            if verbose:
                print(f"Found {len(subscriptions)} accessible subscription(s)")
    
    except Exception as e:
        if verbose:
            print(f"Warning: Could not enumerate subscriptions: {str(e)}")
        if subscription_ids:
            # Fallback: use provided IDs without names
            subscriptions = [(sub_id, "Unknown") for sub_id in subscription_ids]
    
    return subscriptions


def create_summary_data(results: List[SubscriptionScanResult]) -> Dict:
    """Create summary statistics"""
    total = len(results)
    successful = len([r for r in results if r.success])
    failed = total - successful
    
    all_instances = []
    for result in results:
        all_instances.extend(result.redis_instances)
    
    oss_count = len([i for i in all_instances if i.redis_type == "OSS"])
    enterprise_count = len([i for i in all_instances if i.redis_type == "Enterprise"])
    managed_count = len([i for i in all_instances if i.redis_type == "Managed"])
    
    return {
        'Total Subscriptions Attempted': total,
        'Successfully Scanned': successful,
        'Failed Scans': failed,
        'Total Redis Instances': len(all_instances),
        'Azure Cache for Redis (OSS)': oss_count,
        'Azure Cache for Redis Enterprise': enterprise_count,
        'Azure Managed Redis': managed_count
    }


def save_results_excel(results: List[SubscriptionScanResult], output_path: Path):
    """Save results to Excel file"""
    # Collect all instances
    all_instances = []
    for result in results:
        all_instances.extend([inst.to_dict() for inst in result.redis_instances])
    
    # Create summary
    summary = create_summary_data(results)
    summary_df = pd.DataFrame([summary])
    
    # Create failed subscriptions list
    failed_subs = []
    for result in results:
        if not result.success:
            failed_subs.append({
                'Subscription ID': result.subscription_id,
                'Subscription Name': result.subscription_name,
                'Error Type': result.error_type,
                'Error Message': result.error_message,
                'Timestamp': datetime.now().isoformat()
            })
    
    # Write to Excel
    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        # Sheet 1: Redis Instances
        if all_instances:
            instances_df = pd.DataFrame(all_instances)
            instances_df.to_excel(writer, sheet_name='Redis Instances', index=False)
        else:
            pd.DataFrame({'Message': ['No Redis instances discovered']}).to_excel(
                writer, sheet_name='Redis Instances', index=False)
        
        # Sheet 2: Summary
        summary_df.to_excel(writer, sheet_name='Scan Summary', index=False)
        
        # Sheet 3: Failed Subscriptions
        if failed_subs:
            failed_df = pd.DataFrame(failed_subs)
            failed_df.to_excel(writer, sheet_name='Failed Subscriptions', index=False)
        else:
            pd.DataFrame({'Message': ['All subscriptions scanned successfully']}).to_excel(
                writer, sheet_name='Failed Subscriptions', index=False)


def save_results_csv(results: List[SubscriptionScanResult], output_path: Path):
    """Save results to CSV file"""
    all_instances = []
    for result in results:
        all_instances.extend([inst.to_dict() for inst in result.redis_instances])
    
    if all_instances:
        df = pd.DataFrame(all_instances)
        df.to_csv(output_path, index=False)
    else:
        pd.DataFrame({'Message': ['No Redis instances discovered']}).to_csv(output_path, index=False)


def save_results_json(results: List[SubscriptionScanResult], output_path: Path):
    """Save results to JSON file"""
    output_data = {
        'summary': create_summary_data(results),
        'instances': [],
        'failed_subscriptions': []
    }
    
    for result in results:
        output_data['instances'].extend([inst.to_dict() for inst in result.redis_instances])
        
        if not result.success:
            output_data['failed_subscriptions'].append({
                'subscription_id': result.subscription_id,
                'subscription_name': result.subscription_name,
                'error_type': result.error_type,
                'error_message': result.error_message
            })
    
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)


def print_summary(results: List[SubscriptionScanResult], verbose: bool):
    """Print summary to console"""
    summary = create_summary_data(results)
    
    print("\n" + "="*60)
    print("SCAN SUMMARY")
    print("="*60)
    print(f"Total Subscriptions Attempted: {summary['Total Subscriptions Attempted']}")
    print(f"Successfully Scanned: {summary['Successfully Scanned']}")
    print(f"Failed Scans: {summary['Failed Scans']}")
    print(f"\nTotal Redis Instances Discovered: {summary['Total Redis Instances']}")
    print(f"  - Azure Cache for Redis (OSS): {summary['Azure Cache for Redis (OSS)']}")
    print(f"  - Azure Cache for Redis Enterprise: {summary['Azure Cache for Redis Enterprise']}")
    print(f"  - Azure Managed Redis: {summary['Azure Managed Redis']}")
    
    if verbose and summary['Failed Scans'] > 0:
        print("\nFailed Subscriptions:")
        for result in results:
            if not result.success:
                print(f"  ✗ {result.subscription_id} ({result.subscription_name}): {result.error_type} - {result.error_message}")
    
    print("="*60)


def main():
    parser = argparse.ArgumentParser(
        description='Discover Azure Redis instances with minimal permissions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan all accessible subscriptions
  python discoverRedisSKUs.py
  
  # Scan specific subscriptions
  python discoverRedisSKUs.py -s "sub1-id,sub2-id"
  
  # Verbose output with custom location
  python discoverRedisSKUs.py -v -o /path/to/output.xlsx
  
  # JSON output for automation
  python discoverRedisSKUs.py -f json -o inventory.json
        """
    )
    
    parser.add_argument(
        "-s", "--subscriptions",
        dest="subscriptions",
        help="Comma-separated list of subscription IDs to scan (optional, scans all accessible if not provided)",
        metavar="IDS"
    )
    
    parser.add_argument(
        "-o", "--output",
        dest="output",
        default="RedisSKUInventory.xlsx",
        help="Output file path (default: RedisSKUInventory.xlsx)",
        metavar="PATH"
    )
    
    parser.add_argument(
        "-a", "--include-amr",
        dest="include_amr",
        action="store_true",
        default=True,
        help="Include Azure Managed Redis instances (default: True)"
    )
    
    parser.add_argument(
        "--exclude-amr",
        dest="include_amr",
        action="store_false",
        help="Exclude Azure Managed Redis instances"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        dest="verbose",
        action="store_true",
        help="Show detailed progress and errors"
    )
    
    parser.add_argument(
        "-f", "--format",
        dest="format",
        choices=['excel', 'csv', 'json'],
        default='excel',
        help="Output format (default: excel)"
    )
    
    args = parser.parse_args()
    
    # Parse subscription IDs if provided
    subscription_ids = None
    if args.subscriptions:
        subscription_ids = [s.strip() for s in args.subscriptions.split(',')]
    
    # Determine output path
    output_path = Path(args.output)
    
    # Ensure correct file extension
    if args.format == 'excel' and not output_path.suffix == '.xlsx':
        output_path = output_path.with_suffix('.xlsx')
    elif args.format == 'csv' and not output_path.suffix == '.csv':
        output_path = output_path.with_suffix('.csv')
    elif args.format == 'json' and not output_path.suffix == '.json':
        output_path = output_path.with_suffix('.json')
    
    print("Azure Redis SKU Discovery Tool")
    print("="*60)
    print(f"Output: {output_path}")
    print(f"Format: {args.format}")
    print(f"Include AMR: {args.include_amr}")
    print("="*60)
    
    # Authenticate
    try:
        credential = DefaultAzureCredential()
        if args.verbose:
            print("Authentication: Using DefaultAzureCredential")
    except Exception as e:
        print(f"Error: Failed to authenticate: {str(e)}")
        print("Please run 'az login' or configure Azure credentials")
        sys.exit(1)
    
    # Get subscriptions
    subscriptions = get_accessible_subscriptions(credential, subscription_ids, args.verbose)
    
    if not subscriptions:
        print("Error: No accessible subscriptions found")
        sys.exit(1)
    
    print(f"\nScanning {len(subscriptions)} subscription(s) for Redis instances...")
    print()
    
    # Scan each subscription
    results = []
    for sub_id, sub_name in subscriptions:
        display_name = f"{sub_id} ({sub_name})" if sub_name != "Unknown" else sub_id
        
        if args.verbose:
            print(f"Scanning subscription: {display_name}")
        else:
            print(f"  Scanning {display_name}...", end="", flush=True)
        
        result = scan_subscription(credential, sub_id, sub_name, args.include_amr, args.verbose)
        results.append(result)
        
        if not args.verbose:
            if result.success:
                print(f" ✓ Found {len(result.redis_instances)} instance(s)")
            else:
                print(f" ✗ {result.error_type}")
    
    # Save results
    print(f"\nSaving results to {output_path}...")
    try:
        if args.format == 'excel':
            save_results_excel(results, output_path)
        elif args.format == 'csv':
            save_results_csv(results, output_path)
        elif args.format == 'json':
            save_results_json(results, output_path)
        
        print(f"✓ Results saved successfully")
    except Exception as e:
        print(f"Error: Failed to save results: {str(e)}")
        sys.exit(1)
    
    # Print summary
    print_summary(results, args.verbose)
    
    # Exit with appropriate code
    summary = create_summary_data(results)
    if summary['Failed Scans'] > 0:
        sys.exit(2)  # Partial success
    else:
        sys.exit(0)  # Full success


if __name__ == "__main__":
    main()
