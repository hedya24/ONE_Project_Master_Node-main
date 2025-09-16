from mongoengine import Document, StringField, DateTimeField
from datetime import datetime


class AnomalyClass(Document):
    slave_node_ip = StringField(required=True)
    anomaly_type = StringField(required=True)
    timestamp = DateTimeField(required=True, default=datetime.utcnow)
    vehicle_brand = StringField(required=True)
    vehicle_model = StringField(required=True)
    vehicle_color = StringField(required=True)

    meta = {"collection": "anomalies"}

    @classmethod
    def save_anomaly(cls, anomaly_data):

        try:
            anomaly = cls(
                slave_node_ip=anomaly_data["slave_node_ip"],
                anomaly_type=anomaly_data["anomaly_type"],
                timestamp=datetime.strptime(anomaly_data["timestamp"], "%Y-%m-%d %H:%M:%S"),
                vehicle_brand=anomaly_data["vehicle_brand"],
                vehicle_model=anomaly_data["vehicle_model"],
                vehicle_color=anomaly_data["vehicle_color"],
            )
            anomaly.save()
            return True
        except Exception as e:
            print(f"Error saving anomaly: {e}")
            return False