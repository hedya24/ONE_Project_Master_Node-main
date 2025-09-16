from mongoengine import Document, StringField, DateTimeField, EmbeddedDocument, EmbeddedDocumentField, FloatField
from datetime import datetime


class Position(EmbeddedDocument):
    latitude = StringField(required=True)
    longitude = StringField(required=True)


class TrackingClass(Document):
    tracking_id = StringField(required=True, unique=True)
    original_node_ip = StringField(required=True)
    last_found_node_ip = StringField(required=True)
    last_found_position = EmbeddedDocumentField(Position, required=True)
    finishing_timestamp = DateTimeField(required=True, default=datetime.utcnow)
    vehicle_brand = StringField(required=True)
    vehicle_model = StringField(required=True)
    vehicle_color = StringField(required=True)

    meta = {"collection": "trackings"}

    @classmethod
    def save_tracking(cls, tracking_data):
        try:
            tracking = cls(
                tracking_id=tracking_data["task_id"],
                original_node_ip=tracking_data["original_node_ip"],
                last_found_node_ip=tracking_data["last_found_node_ip"],
                last_found_position=Position(
                    latitude=tracking_data["latitude"],
                    longitude=tracking_data["longitude"]
                ),
                finishing_timestamp=datetime.strptime(tracking_data["timestamp"], "%Y-%m-%d %H:%M:%S"),
                vehicle_brand=tracking_data["vehicle_brand"],
                vehicle_model=tracking_data["vehicle_model"],
                vehicle_color=tracking_data["vehicle_color"]
            )
            tracking.save()
            return True
        except Exception as e:
            print(f"Error saving tracking: {e}")
            return False


