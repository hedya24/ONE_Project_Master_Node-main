# 🚗 Intelligent Traffic Surveillance System — Controller Node Project 🚗 

## 📖 Overview

This project implements a **distributed, scalable, and intelligent traffic surveillance system** designed to enhance real-time vehicle tracking and anomaly detection in urban environments. 

By leveraging **containerized microservices**, **Kubernetes orchestration**, and **cloud-native architecture**, the system ensures high efficiency, resilience, and dynamic scalability.

The system is divided into **two main repositories**:

- 🟢 **Master Node Repository**
- 🟡 **Slave Node Repository**

Together, these repositories form the core of the **Controller Node**, enabling seamless coordination between traffic monitoring **Camera Nodes**, distributed processing units (**Slave Nodes**), and the central system orchestrator (**Master Node**).

---

## 📁 Repository Structure and Composition

### 🟢 **Master Node Repository**

The **Master Node** acts as the central brain of the system. Its responsibilities include:

✅ Central task coordination  
✅ Dynamic scaling and provisioning of Slave Nodes  
✅ Database management and historical data logging  
✅ Communication with Slave Nodes and authorities  

**Repository contents:**

| Path/Name               | Description                                                                 |
|-------------------------|-----------------------------------------------------------------------------|
| `classes/`              | Python modules for MongoDB interaction and processing logic.               |
| ├─ `anomalies.py`       | Anomaly management logic.                                                   |
| ├─ `nodes.py`           | Node management and metadata handling.                                      |
| └─ `trackings.py`       | Vehicle tracking operations.                                                |
| `parameters/`           | Database connection parameters.                                             |
| ├─ `credentials.py`     | Access credentials.                                                         |
| └─ `databasemanager.py` | MongoDB connection logic.                                                   |
| `app.py`                | Main Flask application (Master Node logic).                                 |
| `deployment.yaml`       | Kubernetes deployment and HPA configuration for the Master Pod.             |
| `service.yaml`          | Kubernetes service configuration for network exposure.                      |
| `initDB.yaml`           | MongoDB database initialization script.                                     |
| `networkconfigs.txt`    | Commands for cert-manager and NGINX Ingress configuration.                  |
| `Redis_Mongo.txt`       | Commands to deploy Bitnami Redis and MongoDB.                               |
| `update-ingress.sh`     | Shell script to automate MasterPod and Service deployment with Ingress IP.  |
| `Dockerfile`            | Container build instructions for the Master Node.                           |
| `requirements.txt`      | Python dependencies for the Master Node.                                    |

---

### 🟡 **Slave Node Repository**

The **Slave Nodes** are distributed processing units interfacing with Camera Nodes. Their responsibilities include:

✅ Processing vehicle tracking and anomaly detection requests  
✅ Communicating with Camera Nodes and the Master Node  
✅ Enabling distributed and scalable tracking operations  

**Repository contents:**

| Path/Name           | Description                                                      |
|---------------------|------------------------------------------------------------------|
| `app.py`            | Main Flask application running the Slave Node logic.            |
| `Dockerfile`        | Container build instructions for the Slave Node.                |
| `requirements.txt`  | Python dependencies for the Slave Node.                         |

---

## 🔀 Repository Navigation

- ➡️ the [**Master Node repository**](https://github.com/aymenbouallagui/ONE_Project_Master_Node) to deploy and configure the central control logic.
- ➡️ the [**Slave Node repository**](https://github.com/aymenbouallagui/ONE_Project_Slave_Node-) to deploy individual processing nodes for distributed tracking.

The two repositories work together as part of a **Kubernetes-managed infrastructure**.

---
## 🚀 Deployment and Running (Azure AKS Only)

This project is designed **exclusively** for deployment on **Azure Kubernetes Service (AKS)**. Follow the steps below to set up and run the system.

---

### ⚙️ Prerequisites

- Existing AKS cluster with at least one node pool  
- Azure CLI (`az`) installed and authenticated  
- `kubectl` installed and configured for your AKS cluster  
- Sufficient permissions to assign roles in your Azure subscription  

---

### 🏗️ Step-by-Step Deployment

#### 1. Adapt the Code to Your Environment

Update the project files with:

- Your **Azure Subscription ID**  
- Your **AKS Cluster Name**  
- Your **Node Pool Name**  

---

#### 2. Retrieve Managed Identity of the AKS Cluster

```bash
az aks show --name <AKS_CLUSTER_NAME> --resource-group <RESOURCE_GROUP_NAME> --query "identityProfile.kubeletidentity.clientId" -o tsv
```

⚠️ Replace `<AKS_CLUSTER_NAME>` and `<RESOURCE_GROUP_NAME>` with your actual values.

---

#### 3. Assign Required Roles to the Managed Identity

The following roles must be assigned to the AKS managed identity for the MasterPod to operate correctly:

- Contributor  
- Reader  
- Azure Kubernetes Service RBAC Cluster Admin  
- Classic Virtual Machine Contributor  
- Azure Kubernetes Service RBAC Admin  
- Azure Kubernetes Service Arc Cluster Admin Role  

Example for assigning the **Contributor** role:

```bash
az role assignment create --assignee <MANAGED_IDENTITY_CLIENT_ID> --role "Contributor" --scope /subscriptions/<SUBSCRIPTION_ID>
```

Repeat for each role listed above, changing `"Contributor"` to the respective role name.

---

#### 4. Deploy NGINX Ingress and Cert-Manager

Use the provided `networkconfigs.txt` file for the required commands. Run them sequentially.

Example of applying a command from the file:

```bash
kubectl apply -f https://raw.githubusercontent.com/cert-manager/cert-manager/release-1.11/deploy/manifests/cert-manager.yaml
```

⚠️ Ensure all required components are properly deployed before continuing.

---

#### 5. Deploy Redis and MongoDB for the Master Node

The required commands are provided in `Redis_Mongo.txt`. Run them one by one.

Example of deploying Redis with Helm:

Adapt resource names or parameters as needed.

---

#### 6. Deploy the MasterPod

Run the provided automation script:

```bash
bash update-ingress.sh
```

This script:

- ✅ Applies the `deployment.yaml` and `service.yaml` for the MasterPod  
- ✅ Retrieves the NGINX Ingress IP  
- ✅ Updates the service configuration accordingly  

---

#### 7. Retrieve the NGINX Ingress Public IP

```bash
kubectl get services -n ingress-nginx
```

Look for the external IP of the NGINX Ingress controller.

---

#### 8. Access the MasterPod

The MasterPod will be available at:

```
https://<NGINX_INGRESS_IP>.nip.io:443
```

Use this URL to interact with the Master's public API endpoints.

⚠️ **Important:** Secure endpoints require the Admin Token provided in the code. Ensure you use it for authentication when interacting with protected routes.

---

#### 9. Deploy SlavePods and Configure CameraNodes

Deploy SlavePods and configure CameraNodes via the Master's API using tools like:

- 🛠️ Postman  
- 🛠️ curl  

Example of registering a Slave Node via `curl`:

```bash
curl -X POST https://<NGINX_INGRESS_IP>.nip.io:443/register-slave \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"camera_node_ip": "<CAMERA_NODE_IP>", "location": {"latitude": 40.7128, "longitude": -74.0060}}'
```

⚠️  **Important:** Replace placeholders with real values from your environment.

⚠️  **Important:** The master and slave pods accept only https requests with tokens .

⚠️  **Important:** The master has one administrative token written in the code , the slave token is given back after a new deployment request using the /status endpoint

⚠️  **Important:**  please check the needed data of each api before communicating with it .

⚠️  **Important:**  Any changement of the code require new images creation and exposing on dockerhub as public images .

⚠️  **Important:** Not all APIs of master and slave are exposed for external communication the majority is dedicated to internal communication , so please verify the external endpoints of the master in "service.yaml" and the same for the slave in app.py of the master (you can find external slave endpoints) 

⚠️  **Important:** for a real word implementation you have to uncomment the CameraNode communication part of the slave and create a new image of the slavepod also change the master to deploy the new slave image and create also a new master image that get deploy only this new slaveimage and change the deployment file of the master to deploy the new master image (this process is for all code modifications and not only this one) 

For extra-infos : aymenbouallagui19@gmail.com 
---
