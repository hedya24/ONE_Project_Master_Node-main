from mongoengine import Document, StringField, DateTimeField, EmbeddedDocument, EmbeddedDocumentField, FloatField
from datetime import datetime
from geopy.distance import geodesic


class Position(EmbeddedDocument):
    lat = FloatField(required=True)
    long = FloatField(required=True)


class NodeClass(Document):
    node_id = StringField(required=True, unique=True)
    position = EmbeddedDocumentField(Position, required=True)
    date_and_time = DateTimeField(required=True, default=datetime.utcnow)
    ip_address = StringField(required=True, unique=True)
    external_ip = StringField(unique=True)
    name = StringField(required=True, unique=True)
    meta = {
        "collection": "nodes"
    }

    @classmethod
    def save_new_node(cls, node_data):
        position_data = node_data["position"]
        new_node = cls(
            node_id=node_data["node_id"],
            position=Position(
                lat=position_data["lat"],
                long=position_data["long"]
            ),
            ip_address=node_data["ip_address"],
            external_ip=node_data["external_ip"],
            name=node_data["node_name"]
        )
        new_node.save()
        return new_node

    @classmethod
    def get_nodes_in_radius(cls, position, max_distance=300):
        ref_point = (position["lat"], position["long"])
        ip_addresses = []
        all_nodes = cls.objects()
        for node in all_nodes:
            node_point = (node.position.lat, node.position.long)
            distance = geodesic(ref_point, node_point).meters
            if distance <= max_distance:
                ip_addresses.append(node.ip_address)
        return ip_addresses


    @classmethod
    def get_position_by_ip(cls, ip_address):
        node = cls.objects(ip_address=ip_address).first()
        if node:
            return {"lat": node.position.lat, "long": node.position.long}
        return None

    @classmethod
    def get_nearest_node(cls, position):
        ref_point = (position["lat"], position["long"])
        nearest_node = None
        shortest_distance = float("inf")
        for node in cls.objects():
            node_point = (node.position.lat, node.position.long)
            distance = geodesic(ref_point, node_point).meters
            if distance < shortest_distance:
                shortest_distance = distance
                nearest_node = node
        return nearest_node

    @classmethod
    def delete_node_by_name(cls, node_name):
        node = cls.objects(name=node_name).first()
        if node:
            node.delete()
            return True
        return False
