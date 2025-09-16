import os
import subprocess
from flask import Flask, request, jsonify
from kubernetes import client, config
import time
import threading
import logging
from celery import Celery, chain
import redis
from redis.sentinel import Sentinel
from azure.identity import DefaultAzureCredential
from azure.mgmt.containerservice import ContainerServiceClient
import requests
from classes.trackings import TrackingClass
from parameters.credentials import *
from parameters.databasemanager import Database_manager
from classes.nodes import NodeClass
from classes.anomalies import AnomalyClass
from kubernetes.client import AutoscalingV2Api
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


#-------------- needed for crypto token -------------
import jwt
from datetime import datetime, timedelta
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from functools import wraps
#-----------------------------------------------------

db = Database_manager(db_name=db_name, uri=uri)
db.connect_db()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def make_celery(app):
    sentinel = Sentinel([('redis-master-pod-service', 26379)], socket_timeout=0.1)
    redis_master = sentinel.master_for('mymaster', socket_timeout=0.1)

    # Extract host and port from the Redis connection
    connection = redis_master.connection_pool.get_connection('')
    redis_host = connection.host
    redis_port = connection.port

    celery = Celery(
        app.import_name,
        broker=f'redis://{redis_host}:{redis_port}/0',
        backend=f'redis://{redis_host}:{redis_port}/0'
    )
    celery.conf.update(app.config)
    celery.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
    )

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


app = Flask(__name__)
celery = make_celery(app)

# Redis Sentinel and master setup
sentinel = Sentinel([('redis-master-pod-service', 26379)], socket_timeout=0.1)
redis_master = sentinel.master_for('mymaster', socket_timeout=0.1)

# Extract host and port from the Redis connection
connection = redis_master.connection_pool.get_connection('')
redis_host = connection.host
redis_port = connection.port
nginx_ip = os.getenv("NGINX_INGRESS_IP")
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri=f"redis://{redis_host}:{redis_port}/0",
    default_limits=[]
)

redis_client = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True)

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error": "rate_limit_exceeded",
        "message": f"Too many requests. Try again in {e.description.split(' ')[-2]} seconds"
    }), 429



try:
    config.load_kube_config()  # For local development kube version (local production)
except config.config_exception.ConfigException:
    config.load_incluster_config()  # For Kubernetes cluster deployment
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()
autoscaling_v2 = AutoscalingV2Api()
networking_v1 = client.NetworkingV1Api()
custom_objects_api = client.CustomObjectsApi()
subscription_id = "dfdaf337-3eb0-4e94-a946-93bde1e263fd"
resource_group_name = "Progetto_Hedya"
aks_cluster_name = "MyAKSCluster"
node_pool_name = "nodepool1"
credential = DefaultAzureCredential()
container_service_client = ContainerServiceClient(
    credential, subscription_id, api_version="2024-09-01"
)
master_node_service_name = "master-node-app-service"

# ========================= Crypto token setup ===============================
JWT_ALGORITHM = 'RS256'
ACCESS_TOKEN_EXPIRES = timedelta(hours=2400)
if not redis_client.get('jwt_private_key'):
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    redis_client.set('jwt_private_key', private_pem)
    redis_client.set('jwt_public_key', public_pem)

PRIVATE_KEY = redis_client.get('jwt_private_key')
PUBLIC_KEY = redis_client.get('jwt_public_key')


class AuthManager:
    @staticmethod
    def generate_camera_token(camera_ip, slave_node_ip):
        payload = {
            'iss': 'traffic-master-node',
            'sub': camera_ip,
            'aud': slave_node_ip,
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + ACCESS_TOKEN_EXPIRES,
            'scope': 'camera_api'
        }
        token = jwt.encode(payload, PRIVATE_KEY, algorithm=JWT_ALGORITHM)
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return token


@celery.task(name='tasks.renew_camera_token_task', bind=True)
def renew_camera_token_task(self, token, aud):
    try:
        # Decode without verification to check expiration first
        unverified = jwt.decode(token, options={"verify_signature": False})

        if datetime.utcnow() > datetime.fromtimestamp(unverified['exp']):
            return {"error": "Token has expired", "code": 401}

        payload = jwt.decode(token, PUBLIC_KEY, algorithms=[JWT_ALGORITHM], audience=aud, issuer='traffic-master-node')
        new_payload = {
            **payload,
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + ACCESS_TOKEN_EXPIRES
        }

        new_token = jwt.encode(new_payload, PRIVATE_KEY, algorithm=JWT_ALGORITHM)
        return {
            "access_token": new_token.decode('utf-8') if isinstance(new_token, bytes) else new_token,
            "expires_in": ACCESS_TOKEN_EXPIRES.total_seconds(),
            "code": 200
        }
    except Exception as e:
        return {"error": str(e), "code": 401}


@app.route("/auth/camera/sync-renew", methods=["POST"])
@limiter.limit("3 per minute")
def sync_renew_camera_token():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Invalid Authorization header"}), 401
    token = auth_header.split(" ")[1]
    data = request.get_json()
    aud = data.get("audience")
    task_res = renew_camera_token_task.delay(token, aud)
    result = task_res.get()
    if "error" in result:
        return jsonify({"error": result["error"]}), result.get("code", 401)
    return jsonify({
        "access_token": result["access_token"],
        "expires_in": result["expires_in"]
    }), 200


ADMIN_TOKEN = "my_super_very_secret_key_123!"


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header or auth_header != f"Bearer {ADMIN_TOKEN}":
            return jsonify({"error": "Admin access denied"}), 403
        return f(*args, **kwargs)
    return decorated

# ================================================================================
@celery.task(name='tasks.get_nodes_in_radius_task', bind=True)
def get_nodes_in_radius_task(self, ip_address):
    position = NodeClass.get_position_by_ip(ip_address)
    if position is None:
        return None
    return NodeClass.get_nodes_in_radius(position)


@celery.task(name='tasks.get_position_by_ip_task')
def get_position_by_ip_task(ip_address):
    return NodeClass.get_position_by_ip(ip_address)


@celery.task(name='tasks.save_anomaly_task', bind=True)
def save_anomaly_task(self, anomaly_data):
    return AnomalyClass.save_anomaly(anomaly_data)


@celery.task(name='tasks.save_tracking_task', bind=True)
def save_tracking_task(self, tracking_data):
    return TrackingClass.save_tracking(tracking_data)


@celery.task(name='tasks.assign_tracking_task_async', bind=True)
def assign_tracking_task_async(self, tracking_data):
    try:
        position = tracking_data["position"]
        nearest_node_ip = NodeClass.get_nearest_node(position)
        slave_ip = nearest_node_ip.ip_address
        tracking_payload = {
            "vehicle_brand": tracking_data["vehicle_brand"],
            "vehicle_model": tracking_data["vehicle_model"],
            "vehicle_color": tracking_data["vehicle_color"]
        }
        reid_url = f"http://{slave_ip}:80/reid_request"
        requests.post(reid_url, json=tracking_payload, timeout=5, verify=False)
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        raise


@app.route("/nodes_in_radius", methods=["POST"])
def get_nodes_in_radius():
    data = request.get_json()
    ip = data["ip_address"]
    result = get_nodes_in_radius_task.delay(ip)
    return jsonify({"ip_addresses": result.get()})


@app.route("/give_ip_get_position", methods=["POST"])
def give_ip_get_position():
    data = request.get_json()
    ip = data["ip_address"]
    result = get_position_by_ip_task.delay(ip)
    return jsonify(result.get())


def update_task(task_id, status):
    redis_client.hset(f"background_tasks:{task_id}", mapping=status)


def update_deletion_task(task_id, status):
    redis_client.hset(f"deletion_tasks:{task_id}", mapping={
        **status,
        "last_updated": time.time()
    })
    redis_client.expire(f"deletion_tasks:{task_id}", 86400)


def clean_old_redis_tasks():
    while True:
        now = time.time()
        two_hours_ago = now - 2 * 60 * 60
        for key_pattern in ["background_tasks:*", "deletion_tasks:*"]:
            for key in redis_client.scan_iter(key_pattern):
                task_data = redis_client.hgetall(key)
                timestamp = float(task_data.get("timestamp") or task_data.get("created_at") or 0)
                if timestamp and timestamp < two_hours_ago:
                    redis_client.delete(key)
                    logger.info(f"Deleted old Redis key: {key}")
        time.sleep(600)


threading.Thread(target=clean_old_redis_tasks, daemon=True).start()


@celery.task(name='tasks.scale_node_pool', bind=True)
def scale_node_pool(*args, **kwargs):
    try:
        node_pool = container_service_client.agent_pools.get(
            resource_group_name, aks_cluster_name, node_pool_name
        )
        node_pool.count += 1
        async_update = container_service_client.agent_pools.begin_create_or_update(
            resource_group_name, aks_cluster_name, node_pool_name, node_pool
        )
        async_update.result()
        timeout = 20
        start_time = time.time()
        while True:
            nodes = core_v1.list_node()
            nodes_sorted = sorted(nodes.items, key=lambda x: x.metadata.creation_timestamp, reverse=True)
            new_node_name = nodes_sorted[0].metadata.name
            node_status = next(
                (status for status in nodes_sorted[0].status.conditions if status.type == "Ready"), None
            )
            if node_status and node_status.status == "True":

                return new_node_name

            if time.time() - start_time > timeout:
                raise TimeoutError("Nodepool time error")

            time.sleep(20)

    except Exception as e:
        logger.error(f"Error scaling nodepool: {e}")
        raise


@app.route("/deploy", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def deploy_flask_app():
    try:
        request_data = request.get_json()
        image = request_data.get("image", "aymen019/onefinalslaveimage1234:v1.0")
        node_data = request_data.get("node_data")
        camera_node_ip = request_data.get("camera_node_ip")
        task_id = str(time.time())
        update_task(task_id, {
            "status": "in-progress",
            "external_ip": "",
            "camera_node_ip": camera_node_ip,
            "camera_token": "",
            "timestamp": time.time()
        })

        chain(
            scale_node_pool.si(),
            deploy_in_background.s(node_data, image, task_id, camera_node_ip)
        ).delay()

        return jsonify({"message": "Deployment started", "task_id": task_id}), 202

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@celery.task(name='tasks.deploy_in_background', bind=True)
def deploy_in_background(self, n_n_name, node_data, image, task_id, camera_node_ip):
    try:
        new_node_name = n_n_name
        node_data = dict(node_data or {})
        node_data["node_name"] = new_node_name
        unique_suffix = new_node_name.replace(".", "-")
        app_label = f"flask-app-{unique_suffix}"
        redis_label = f"redis-{unique_suffix}"
        service_name = f"{app_label}-service"
        deployment_name = f"{app_label}-deployment"

        # Generate JWT token for camera communication
        token = AuthManager.generate_camera_token(
            camera_ip=camera_node_ip,
            slave_node_ip=f"{service_name}.{nginx_ip}"
        )

        # Update task status
        update_task(task_id, {
            "status": "in-progress",
            "external_ip": "",
            "timestamp": time.time()
        })

        # ====== CERT-MANAGER CONFIGURATION ======
        cert_manifest = {
            "apiVersion": "cert-manager.io/v1",
            "kind": "Certificate",
            "metadata": {
                "name": f"{app_label}-cert",
                "namespace": "default"
            },
            "spec": {
                "secretName": f"{app_label}-tls",
                "issuerRef": {
                    "name": "selfsigned-issuer",
                    "kind": "ClusterIssuer"
                },
                "commonName": "slave-node-cert",
                "dnsNames": [f"{service_name}.{nginx_ip}"],
                "privateKey": {
                    "algorithm": "RSA",
                    "size": 2048
                },
                "duration": "8760h",
                "renewBefore": "720h"
            }
        }

        # ====== REDIS DEPLOYMENT USING HELM ======
        def run_command(command_list, success_msg="", error_msg=""):
            try:
                result = subprocess.run(command_list, check=True, capture_output=True, text=True)
                if success_msg:
                    logger.info(success_msg)
                return result.stdout.strip()
            except subprocess.CalledProcessError as e:
                logger.error(f"{error_msg}\n{e.stderr}")
                raise

        def deploy_redis_sentinel(redis_label):
            run_command(["helm", "repo", "add", "bitnami", "https://charts.bitnami.com/bitnami"],
                        success_msg="Bitnami repo added",
                        error_msg="Failed to add Bitnami repo")
            run_command(["helm", "repo", "update"],
                        success_msg="Helm repos updated",
                        error_msg="Failed to update Helm repos")
            run_command([
                "helm", "install", redis_label, "bitnami/redis",
                "--set", "architecture=replication",
                "--set", "sentinel.enabled=true",
                "--set", f"fullnameOverride={redis_label}",
                "--set", "replica.resources.requests.cpu=50m",
                "--set", "replica.resources.requests.memory=64Mi",
                "--set", "auth.enabled=false",
                "--set", f'nodeSelector."kubernetes\\.io/hostname"={new_node_name}'
            ],
                success_msg="Redis Sentinel deployed",
                error_msg="Failed to deploy Redis")
            time.sleep(10)  # Wait for Redis to initialize
            return redis_label

        redis_master_service = str(deploy_redis_sentinel(redis_label))

        # ====== SLAVE APPLICATION CONFIGURATION ======
        app_manifests = [
            # Deployment
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": deployment_name,
                    "labels": {"app": app_label}
                },
                "spec": {
                    "selector": {"matchLabels": {"app": app_label}},
                    "template": {
                        "metadata": {"labels": {"app": app_label}},
                        "spec": {
                            "containers": [{
                                "name": app_label,
                                "image": image,
                                "ports": [{"containerPort": 5000}],
                                "env": [
                                    {"name": "MY_POD_IP", "value": service_name},
                                    {"name": "MY_HOST", "value": f"{service_name}.{nginx_ip}"},
                                    {"name": "CAMERA_NODE_IP", "value": camera_node_ip},
                                    {"name": "MASTER_NODE_SERVICE_NAME", "value": master_node_service_name},
                                    {"name": "REDIS_POD_SERVICE", "value": redis_master_service},
                                    {"name": "JWT_PUBLIC_KEY", "value": PUBLIC_KEY},
                                    {"name": "CAMERA_NODE_TOKEN", "value": token},
                                    {"name": "APP_LABEL", "value": app_label},

                                ]
                            }],
                            "nodeSelector": {"kubernetes.io/hostname": new_node_name}
                        }
                    }
                }
            },
            # ClusterIP Service
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": service_name,
                    "labels": {"app": app_label}
                },
                "spec": {
                    "type": "ClusterIP",
                    "selector": {"app": app_label},
                    "ports": [{
                        "name": "http",
                        "port": 80,
                        "targetPort": 5000
                    }]
                }
            },
            # Ingress
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "Ingress",
                "metadata": {
                    "name": f"{app_label}-ingress",
                    "annotations": {
                        "kubernetes.io/ingress.class": "nginx",
                        "cert-manager.io/cluster-issuer": "selfsigned-issuer",
                        "nginx.ingress.kubernetes.io/ssl-redirect": "true",
                        "nginx.ingress.kubernetes.io/backend-protocol": "HTTP",
                        "nginx.ingress.kubernetes.io/force-ssl-redirect": "true",
                        "nginx.ingress.kubernetes.io/hsts": "true",
                        "nginx.ingress.kubernetes.io/hsts-max-age": "31536000",
                        "nginx.ingress.kubernetes.io/hsts-include-subdomains": "true",
                        "nginx.ingress.kubernetes.io/x-frame-options": "DENY",
                        "nginx.ingress.kubernetes.io/x-content-type-options": "nosniff",
                        "nginx.ingress.kubernetes.io/x-xss-protection": "1; mode=block",
                        "nginx.ingress.kubernetes.io/content-security-policy": "default-src 'self'"

                    }
                },
                "spec": {
                    "tls": [{
                        "hosts": [f"{service_name}.{nginx_ip}"],
                        "secretName": f"{app_label}-tls"
                    }],
                    "rules": [{
                        "host": f"{service_name}.{nginx_ip}",
                        "http": {
                            "paths": [
                                {
                                    "path": "/report_anomaly",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": service_name,
                                            "port": {"number": 80}
                                        }
                                    }
                                },
                                {
                                    "path": "/auth/camera/renew",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": service_name,
                                            "port": {"number": 80}
                                        }
                                    }
                                },
                                {
                                    "path": "/wait_for_response",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": service_name,
                                            "port": {"number": 80}
                                        }
                                    }
                                }
                            ]
                        }
                    }]
                }
            }
        ]

        # HPA Configuration
        hpa_manifest = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": f"{app_label}-hpa"},
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": deployment_name
                },
                "minReplicas": 1,
                "maxReplicas": 10,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": 50
                            }
                        }
                    }
                ]
            }
        }
        deployment_ad = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": f"{app_label}-ad",
                "labels": {"app": f"{app_label}-ad"}
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": f"{app_label}-ad"}},
                "template": {
                    "metadata": {"labels": {"app": f"{app_label}-ad"}},
                    "spec": {
                        "containers": [{
                            "name": "ad-container",
                            "image": "pietroruiu/one-project:wlchar_ad_api_cpu",
                            "ports": [{"containerPort": 6000}],
                            "command": ["bash"],
                            "args": ["-c", "source DemoAD_start.sh"],
                            "resources": {
                                "limits": {
                                    "nvidia.com/gpu": 1
                                }
                            }
                        }],
                        "nodeSelector": {"kubernetes.io/hostname": new_node_name}
                    }
                }
            }
        }

        deployment_vri = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": f"{app_label}-vri",
                "labels": {"app": f"{app_label}-vri"}
            },
            "spec": {
                "replicas": 0,
                "selector": {"matchLabels": {"app": f"{app_label}-vri"}},
                "template": {
                    "metadata": {"labels": {"app": f"{app_label}-vri"}},
                    "spec": {
                        "containers": [{
                            "name": "vri-container",
                            "image": "pietroruiu/one-project:wlchar_reid_api_cpu",
                            "ports": [{"containerPort": 5000}],
                            "env": [
                                {"name": "MAIN_SERVICE", "value": service_name}
                            ],
                            "command": ["bash"],
                            "args": ["-c", "source DemoReID_start.sh"],

                            "resources": {
                                "limits": {
                                    "nvidia.com/gpu": 1
                                }
                            }
                        }],
                        "nodeSelector": {"kubernetes.io/hostname": new_node_name}
                    }
                }
            }
        }

        hpa_ad = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": f"{app_label}-ad-hpa"},  # Changed to -ad
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": f"{app_label}-ad"  # Matches deployment name
                },
                "minReplicas": 1,
                "maxReplicas": 5,
                "metrics": [{
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {"type": "Utilization", "averageUtilization": 60}
                    }
                }]
            }
        }
        hpa_vri = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": f"{app_label}-vri-hpa"},  # Changed to -vri
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": f"{app_label}-vri"  # Matches deployment name
                },
                "minReplicas": 1,
                "maxReplicas": 5,
                "metrics": [{
                    "type": "Resource",
                    "resource": {
                        "name": "memory",
                        "target": {"type": "Utilization", "averageUtilization": 70}
                    }
                }]
            }
        }
        service_ad = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": f"{app_label}-ad-service",
                "labels": {"app": f"{app_label}-ad"}
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": f"{app_label}-ad"},
                "ports": [{
                    "name": "http",
                    "port": 80,
                    "targetPort": 6000
                }]
            }
        }
        service_vri = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": f"{app_label}-vri-service",
                "labels": {"app": f"{app_label}-vri"}
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": f"{app_label}-vri"},
                "ports": [{
                    "name": "http",
                    "port": 80,
                    "targetPort": 7000
                }]
            }
        }
        app_manifests.extend([deployment_ad, deployment_vri, service_ad, service_vri])
        # ====== DEPLOY ALL RESOURCES ======
        # Create certificate
        custom_objects_api.create_namespaced_custom_object(
            group="cert-manager.io",
            version="v1",
            namespace="default",
            plural="certificates",
            body=cert_manifest
        )

        # Deploy Main HPA
        autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(
            namespace="default",
            body=hpa_manifest
        )
        autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(
            namespace="default",
            body=hpa_ad
        )

        autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(
            namespace="default",
            body=hpa_vri
        )

        # Deploy Application components
        for manifest in app_manifests:
            if manifest["kind"] == "Deployment":
                apps_v1.create_namespaced_deployment(namespace="default", body=manifest)
            elif manifest["kind"] == "Service":
                core_v1.create_namespaced_service(namespace="default", body=manifest)
            elif manifest["kind"] == "Ingress":
                networking_v1.create_namespaced_ingress(namespace="default", body=manifest)
            #elif manifest["kind"] == "NetworkPolicy":
                #networking_v1.create_namespaced_network_policy(namespace="default", body=manifest)

        time.sleep(10)

        # Save node information with the external URL
        node_data["ip_address"] = service_name
        node_data["external_ip"] = f"{service_name}.{nginx_ip}"
        NodeClass.save_new_node(node_data)

        update_task(task_id, {
            "status": "complete",
            "external_ip": f"{service_name}.{nginx_ip}",
            "timestamp": time.time(),
            "camera_token": token
        })


    except Exception as e:
        logger.error(f"Deployment failed: {str(e)}", exc_info=True)
        update_task(task_id, {
            "status": "error",
            "error": str(e),
            "timestamp": time.time()
        })
        raise

@app.route("/status", methods=["POST"])
@admin_required
@limiter.limit("50 per minute")
def check_status():
    data = request.get_json()
    task_id = data["task_id"]
    result = redis_client.hgetall(f"background_tasks:{task_id}")
    if not result:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(result)


@app.route("/delete_node", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def delete_node():
    try:
        request_data = request.get_json()
        node_name = request_data.get("node_name")
        task_id = str(time.time())
        update_deletion_task(task_id, {
            "node_name": node_name,
            "status": "initiated",
            "created_at": time.time()
        })
        delete_node_in_background.delay(node_name, task_id)
        return jsonify({"message": "Node deletion started", "task_id": task_id}), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@celery.task(name='tasks.delete_related_k8s_resources', bind=True)
def delete_related_k8s_resources(node_name):
    try:
        unique_suffix = node_name.replace(".", "-")
        app_label = f"flask-app-{unique_suffix}"
        redis_label = f"redis-{unique_suffix}"
        deployment_name = f"{app_label}-deployment"
        service_name = f"{app_label}-service"
        hpa_name = f"{app_label}-hpa"
        ingress_name = f"{app_label}-ingress"
        apps_v1.delete_namespaced_deployment(name=deployment_name, namespace="default")
        core_v1.delete_namespaced_service(name=service_name, namespace="default")
        autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler(name=hpa_name, namespace="default")
        networking_v1.delete_namespaced_ingress(name=ingress_name, namespace="default")
        def run_helm_command(command_list, success_msg="", error_msg=""):
            try:
                result = subprocess.run(command_list, check=True, capture_output=True, text=True)
                if success_msg:
                    logger.info(success_msg)
                return result.stdout.strip()
            except subprocess.CalledProcessError as e:
                logger.error(f"{error_msg}\n{e.stderr}")
                raise
        run_helm_command(["helm", "uninstall", redis_label, "--namespace", "default"],
                        success_msg=f"Redis Helm release {redis_label} uninstalled",
                        error_msg=f"Failed to uninstall Redis Helm release {redis_label}")
        NodeClass.delete_node_by_name(node_name)
        logger.info("Deleted all related resources")
    except Exception as e:
        logger.error(f"Failed to delete resources: {e}")


@celery.task(name='tasks.delete_node_in_background', bind=True)
def delete_node_in_background(node_name, task_id):
    try:
        logger.info(f"Deleting node: {node_name}")
        core_v1.delete_node(node_name)
        delete_related_k8s_resources.delay(node_name)
        time.sleep(50)
        scale_down_node_pool.delay()
        update_deletion_task(task_id, {
            "stage": "deleting_resources",
            "status": "completed"
        })
    except Exception as e:
        update_deletion_task(task_id, {
            "stage": "deleting_resources",
            "status": "failed",
            "error": str(e)
        })


@celery.task(name='tasks.scale_down_node_pool', bind=True)
def scale_down_node_pool():
    try:
        node_pool = container_service_client.agent_pools.get(
            resource_group_name, aks_cluster_name, node_pool_name
        )
        if node_pool.count > 1:
            node_pool.count -= 1
        else:
            logger.warning("Node pool already at 0")
        async_update = container_service_client.agent_pools.begin_create_or_update(
            resource_group_name, aks_cluster_name, node_pool_name, node_pool
        )
        async_update.result()

    except Exception as e:
        logger.error(f"Error: {e}")
        raise


@app.route("/check_deletion_task", methods=["POST"])
@admin_required
@limiter.limit("50 per minute")
def check_deletion_task():
    data = request.get_json()
    task_id = data["task_id"]
    result = redis_client.hgetall(f"deletion_tasks:{task_id}")
    if not result:
        return jsonify({"error": "Task ID not found"}), 404
    return jsonify(result)


@app.route("/save_anomaly", methods=["POST"])
def save_anomaly():
    try:
        anomaly_data = request.get_json()
        required_fields = [
            "slave_node_ip",
            "anomaly_type",
            "timestamp",
            "vehicle_brand",
            "vehicle_model",
            "vehicle_color"
        ]
        if not all(field in anomaly_data for field in required_fields):
            return jsonify({"error": "Missing fields"}), 400

        result = save_anomaly_task.delay(anomaly_data)
        if result:
            return jsonify({"message": "Anomaly saved "}), 200
        return jsonify({"error": "Failed "}), 500
    except Exception as e:
        return jsonify({"error": f"Server Error: {str(e)}"}), 500


@app.route("/save_tracking", methods=["POST"])
def save_tracking():
    try:
        tracking_data = request.get_json()
        required_fields = [
            "task_id",
            "original_node_ip",
            "last_found_node_ip",
            "latitude",
            "longitude",
            "timestamp",
            "vehicle_brand",
            "vehicle_model",
            "vehicle_color"
        ]
        if not all(field in tracking_data for field in required_fields):
            return jsonify({"error": "Missing field"}), 400

        result = save_tracking_task.delay(tracking_data)
        if result:
            return jsonify({"message": "Tracking saved successfully"}), 200
        return jsonify({"error": "Failed to save tracking"}), 500

    except Exception as e:
        return jsonify({"error": f"Server Error: {str(e)}"}), 500


@app.route("/assign_tracking_task", methods=["POST"])
@admin_required
@limiter.limit("200 per minute")
def assign_tracking_task():
    try:
        data = request.get_json()
        required_fields = ["vehicle_brand", "vehicle_model", "vehicle_color", "position"]
        if not all(field in data for field in required_fields):
            return jsonify({"error": "Missing field"}), 400
        position = data["position"]
        if not isinstance(position, dict) or "lat" not in position or "long" not in position:
            return jsonify({"error": "Invalid position"}), 400
        assign_tracking_task_async.delay(data)
        return jsonify({"message": "Tracking request performed"}), 202
    except Exception as e:
        return jsonify({"error": f"Server Error: {str(e)}"}), 500


@app.route("/nodes", methods=["GET"]) #temporary for production
def list_nodes():
    nodes = NodeClass.objects().only("ip_address", "external_ip", "name", "position")
    return jsonify([{
        "name": node.name,
        "ip": node.ip_address,
        "external_ip": node.external_ip,
        "lat": node.position.lat,
        "long": node.position.long
    } for node in nodes])


@app.route("/anomalies_log", methods=["GET"]) #temporary for production
def anomalies_log():
    logs = AnomalyClass.objects().order_by('-timestamp')[:50]
    return jsonify([{
        "slave_node_ip": log.slave_node_ip,
        "anomaly_type": log.anomaly_type,
        "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "vehicle": {
            "brand": log.vehicle_brand,
            "model": log.vehicle_model,
            "color": log.vehicle_color
        }
    } for log in logs])


@app.route("/view_trackings", methods=["GET"])#temporary for production
def view_trackings():
    try:
        all_trackings = TrackingClass.objects()
        result = []
        for tracking in all_trackings:
            result.append({
                "tracking_id": tracking.tracking_id,
                "original_node_ip": tracking.original_node_ip,
                "last_found_node_ip": tracking.last_found_node_ip,
                "latitude": tracking.last_found_position.latitude,
                "longitude": tracking.last_found_position.longitude,
                "vehicle_brand": tracking.vehicle_brand,
                "vehicle_model": tracking.vehicle_model,
                "vehicle_color": tracking.vehicle_color,
                "finishing_timestamp": tracking.finishing_timestamp.strftime("%Y-%m-%d %H:%M:%S")
            })
        return jsonify({"trackings": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/active_tasks", methods=["GET"])#temporary for production
def list_active_tasks():
    return jsonify([
        redis_client.hgetall(key)
        for key in redis_client.scan_iter("background_tasks:*")
    ])


if __name__ == "__main__":
    app.run(debug=False)