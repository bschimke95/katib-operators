#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging

from oci_image import OCIImageResource, OCIImageResourceError
from ops.charm import CharmBase, RelationJoinedEvent
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from serialized_data_interface import (
    NoCompatibleVersions,
    NoVersionsListed,
    get_interfaces,
)

logger = logging.getLogger(__name__)


class CheckFailed(Exception):
    """Raise this exception if one of the checks in main fails."""

    def __init__(self, msg, status_type=None):
        super().__init__()

        self.msg = str(msg)
        self.status_type = status_type
        self.status = status_type(self.msg)


class Operator(CharmBase):
    """Deploys the katib-ui service."""

    def __init__(self, framework):
        super().__init__(framework)

        self.image = OCIImageResource(self, "oci-image")
        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)
        self.framework.observe(self.on.leader_elected, self.set_pod_spec)
        self.framework.observe(self.on["ingress"].relation_changed, self.set_pod_spec)
        self.framework.observe(
            self.on.sidebar_relation_joined, self._on_sidebar_relation_joined
        )
        self.framework.observe(
            self.on.sidebar_relation_departed, self._on_sidebar_relation_departed
        )

    def set_pod_spec(self, event):
        try:

            self._check_leader()

            interfaces = self._get_interfaces()

            image_details = self._check_image_details()

        except CheckFailed as check_failed:
            self.model.unit.status = check_failed.status
            return

        self._configure_ingress(interfaces)

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            self.model.unit.status = e.status
            return

        self.model.pod.set_spec(
            {
                "version": 3,
                "serviceAccount": {
                    "roles": [
                        {
                            "global": True,
                            "rules": [
                                {
                                    "apiGroups": [""],
                                    "resources": ["configmaps", "namespaces"],
                                    "verbs": ["*"],
                                },
                                {
                                    "apiGroups": ["kubeflow.org"],
                                    "resources": [
                                        "experiments",
                                        "trials",
                                        "suggestions",
                                    ],
                                    "verbs": ["*"],
                                },
                            ],
                        }
                    ]
                },
                "containers": [
                    {
                        "name": "katib-ui",
                        "command": ["./katib-ui"],
                        "args": [f"--port={self.model.config['port']}"],
                        "imageDetails": image_details,
                        "ports": [
                            {"name": "http", "containerPort": self.model.config["port"]}
                        ],
                        "envConfig": {"KATIB_CORE_NAMESPACE": self.model.name},
                    }
                ],
            }
        )

        self.model.unit.status = ActiveStatus()

    def _configure_ingress(self, interfaces):
        if interfaces["ingress"]:
            interfaces["ingress"].send_data(
                {
                    "prefix": "/katib/",
                    "service": self.model.app.name,
                    "port": self.model.config["port"],
                }
            )

    def _check_leader(self):
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            raise CheckFailed("Waiting for leadership", WaitingStatus)

    def _get_interfaces(self):
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise CheckFailed(err, WaitingStatus)
        except NoCompatibleVersions as err:
            raise CheckFailed(err, BlockedStatus)
        return interfaces

    def _check_image_details(self):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            raise CheckFailed(f"{e.status.message}", e.status_type)
        return image_details

    def _on_sidebar_relation_joined(self, event: RelationJoinedEvent):
        if not self.unit.is_leader():
            return
        event.relation.data[self.app].update(
            {
                "config": json.dumps(
                    [
                        {
                            "app": self.app.name,
                            "position": 5,
                            "type": "item",
                            "link": "/katib/",
                            "text": "Experiments (AutoML)",
                            "icon": "kubeflow:katib",
                        }
                    ]
                )
            }
        )

    def _on_sidebar_relation_departed(self, event):
        if not self.unit.is_leader():
            return
        event.relation.data[self.app].update({"config": json.dumps([])})


if __name__ == "__main__":
    main(Operator)
