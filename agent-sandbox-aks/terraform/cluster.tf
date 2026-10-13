resource "azapi_resource" "cluster" {
  type      = "Microsoft.ContainerService/managedClusters@2025-10-01"
  name      = var.cluster_name
  parent_id = azapi_resource.group.id
  location  = var.location
  tags      = local.tags
  identity { type = "SystemAssigned" }
  body = {
    sku = { name = "Base", tier = "Free" }
    properties = {
      kubernetesVersion    = var.kubernetes_version
      dnsPrefix            = var.cluster_name
      nodeResourceGroup    = "${var.resource_group}-nodes"
      enableRBAC           = true
      disableLocalAccounts = true
      aadProfile = {
        managed         = true
        enableAzureRBAC = true
        tenantID        = var.tenant_id
      }
      apiServerAccessProfile = {
        enablePrivateCluster = false
        authorizedIPRanges   = var.operator_cidrs
      }
      oidcIssuerProfile = { enabled = true }
      securityProfile = {
        workloadIdentity = { enabled = true }
      }
      networkProfile = {
        networkPlugin     = "azure"
        networkPluginMode = "overlay"
        networkDataplane  = "cilium"
        networkPolicy     = "cilium"
        loadBalancerSku   = "standard"
        outboundType      = "loadBalancer"
        serviceCidr       = "10.96.0.0/16"
        dnsServiceIP      = "10.96.0.10"
        podCidr           = "10.244.0.0/16"
        loadBalancerProfile = {
          managedOutboundIPs = { count = 1 }
        }
      }
      agentPoolProfiles = [
        merge(local.pool_common, {
          name            = "platform"
          mode            = "System"
          vmSize          = var.platform_size
          workloadRuntime = "OCIContainer"
          nodeLabels      = { "demo.sandbox/role" = "platform" }
          upgradeSettings = { maxSurge = "1", maxUnavailable = "0" }
          securityProfile = {
            enableSecureBoot = true
            enableVTPM       = true
            sshAccess        = "Disabled"
          }
        }),
        merge(local.pool_common, {
          name            = "kata"
          mode            = "User"
          vmSize          = var.kata_size
          workloadRuntime = "KataVmIsolation"
          nodeLabels      = { "demo.sandbox/role" = "sandbox" }
          nodeTaints      = ["sandbox=kata:NoSchedule"]
          securityProfile = {
            enableSecureBoot = false
            enableVTPM       = false
            sshAccess        = "Disabled"
          }
        })
      ]
    }
  }
  response_export_values = ["properties.provisioningState", "properties.nodeResourceGroup", "properties.fqdn"]
}

output "cluster_id" { value = azapi_resource.cluster.id }
output "resource_group" { value = azapi_resource.group.name }
output "cluster_name" { value = azapi_resource.cluster.name }
