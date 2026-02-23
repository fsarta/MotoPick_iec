#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gRPC Client for PLCnext - MotoPick adaptation
Handles communication with PLCnext controller via gRPC
"""
import grpc
import logging
import json
import os
import xml.etree.ElementTree as ET 

from pxc_grpc.Plc.Gds.IDataAccessService_pb2 import (
    IDataAccessServiceReadSingleRequest,
    IDataAccessServiceWriteSingleRequest
)
from pxc_grpc.Plc.Gds.IDataAccessService_pb2_grpc import IDataAccessServiceStub

# Gestione sicura per l'importazione di GetPortListRequest
try:
    from pxc_grpc.Plc.Gds.IDataAccessService_pb2 import IDataAccessServiceGetPortListRequest
    HAS_GET_PORT_LIST = True
except ImportError:
    HAS_GET_PORT_LIST = False

logger = logging.getLogger(__name__)


class GrpcClient:
    """
    PLCnext gRPC Client - provides read/write access to PLC variables.
    Falls back to simulation mode if gRPC is not available.
    """

    def __init__(self, address: str):
        self.address = address
        self.channel = None
        self.stub = None
        self._connected = False
        self._sim_data = self._init_sim_data()

        if GRPC_AVAILABLE:
            try:
                self._connect()
            except Exception as e:
                logger.error(f"gRPC connection failed: {e}. Switching to simulation mode.")
                self._connected = False
        else:
            logger.info("Running in simulation mode (no gRPC)")

    def _init_sim_data(self) -> dict:
        """Initialize simulation data for all MotoPick variables"""
        data = {
            # System status
            "Arp.Plc.Eclr/MotoPick.System.Running": False,
            "Arp.Plc.Eclr/MotoPick.System.Connected": False,
            "Arp.Plc.Eclr/MotoPick.System.Error": False,
            "Arp.Plc.Eclr/MotoPick.System.ErrorCode": 0,
            "Arp.Plc.Eclr/MotoPick.System.PicksPerMinute": 0.0,
            "Arp.Plc.Eclr/MotoPick.System.TotalPicks": 0,
            "Arp.Plc.Eclr/MotoPick.System.MissedItems": 0,
        }

        # Robot simulation data (up to 8 robots)
        for i in range(1, 9):
            prefix = f"Arp.Plc.Eclr/MotoPick.Robot{i:02d}"
            data.update({
                f"{prefix}.Enabled": i <= 2,
                f"{prefix}.Running": False,
                f"{prefix}.Error": False,
                f"{prefix}.ErrorCode": 0,
                f"{prefix}.PicksPerMinute": 0.0,
                f"{prefix}.TotalPicks": 0,
                f"{prefix}.Efficiency": 0.0,
                f"{prefix}.X": 0.0,
                f"{prefix}.Y": 0.0,
                f"{prefix}.Z": 0.0,
            })

        # Conveyor simulation data (up to 16 conveyors)
        for i in range(1, 17):
            prefix = f"Arp.Plc.Eclr/MotoPick.Conveyor{i:02d}"
            data.update({
                f"{prefix}.Enabled": i <= 2,
                f"{prefix}.Running": False,
                f"{prefix}.Speed": 0.0,
                f"{prefix}.ActualSpeed": 0.0,
                f"{prefix}.ItemsDetected": 0,
                f"{prefix}.ItemsLeft": 0,
            })

        return data

    def _connect(self):
        """Establish gRPC connection"""
        if self.address.startswith('unix://'):
            credentials = grpc.local_channel_credentials()
            self.channel = grpc.secure_channel(self.address, credentials)
        else:
            self.channel = grpc.insecure_channel(self.address)

        self.stub = plcnext_pb2_grpc.DataAccessServiceStub(self.channel)
        self._connected = True
        logger.info(f"gRPC connected to {self.address}")

    @property
    def is_connected(self) -> bool:
        return self._connected and GRPC_AVAILABLE

    def read_single(self, port_name: str) -> dict:
        """Read a single variable"""
        if not self.is_connected:
            # Simulation mode
            value = self._sim_data.get(port_name, None)
            return {
                "port_name": port_name,
                "value": value,
                "success": True,
                "simulated": True
            }

        try:
            request = plcnext_pb2.ReadRequest(portNames=[port_name])
            response = self.stub.Read(request)
            if response.dataItems:
                item = response.dataItems[0]
                return {
                    "port_name": port_name,
                    "value": self._extract_value(item),
                    "success": True,
                    "simulated": False
                }
            return {"port_name": port_name, "value": None, "success": False}
        except Exception as e:
            logger.error(f"Read error for {port_name}: {e}")
            return {"port_name": port_name, "value": None, "success": False, "error": str(e)}

    def read_multiple(self, port_names: list) -> list:
        """Read multiple variables at once"""
        if not self.is_connected:
            return [
                {
                    "port_name": name,
                    "value": self._sim_data.get(name, None),
                    "success": True,
                    "simulated": True
                }
                for name in port_names
            ]

        try:
            request = plcnext_pb2.ReadRequest(portNames=port_names)
            response = self.stub.Read(request)
            results = []
            for item in response.dataItems:
                results.append({
                    "port_name": item.portName,
                    "value": self._extract_value(item),
                    "success": True,
                    "simulated": False
                })
            return results
        except Exception as e:
            logger.error(f"Read multiple error: {e}")
            return [{"port_name": name, "value": None, "success": False, "error": str(e)} for name in port_names]

    def write_single(self, port_name: str, value, data_type: str = "AUTO") -> bool:
        """Write a single variable"""
        if not self.is_connected:
            # Update simulation data
            self._sim_data[port_name] = value
            logger.debug(f"[SIM] Write {port_name} = {value}")
            return True

        try:
            typed_value = self._create_typed_value(value, data_type)
            item = plcnext_pb2.DataItem(portName=port_name, value=typed_value)
            request = plcnext_pb2.WriteRequest(dataItems=[item])
            response = self.stub.Write(request)
            return True
        except Exception as e:
            logger.error(f"Write error for {port_name}={value}: {e}")
            return False

    def discover_axes(self) -> list:
        """Discover available axes - returns list for compatibility"""
        # MotoPick doesn't use axes in the same way, but we keep this for compatibility
        return []

    def _extract_value(self, item):
        """Extract Python value from protobuf DataItem"""
        try:
            val = item.value
            which = val.WhichOneof('value')
            if which == 'boolValue':
                return val.boolValue
            elif which == 'int8Value':
                return val.int8Value
            elif which == 'int16Value':
                return val.int16Value
            elif which == 'int32Value':
                return val.int32Value
            elif which == 'int64Value':
                return val.int64Value
            elif which == 'uint8Value':
                return val.uint8Value
            elif which == 'uint16Value':
                return val.uint16Value
            elif which == 'uint32Value':
                return val.uint32Value
            elif which == 'uint64Value':
                return val.uint64Value
            elif which == 'floatValue':
                return round(float(val.floatValue), 6)
            elif which == 'doubleValue':
                return round(float(val.doubleValue), 6)
            elif which == 'stringValue':
                return val.stringValue
            else:
                return None
        except Exception as e:
            logger.warning(f"Value extraction error: {e}")
            return None

    def _create_typed_value(self, value, data_type: str):
        """Create a typed protobuf value from Python value"""
        try:
            typed = plcnext_pb2.TypedValue()
            dt = data_type.upper()

            if dt == 'BOOL':
                typed.boolValue = bool(value)
            elif dt in ('INT', 'INT16'):
                typed.int16Value = int(value)
            elif dt in ('DINT', 'INT32'):
                typed.int32Value = int(value)
            elif dt in ('UINT', 'UINT16'):
                typed.uint16Value = int(value)
            elif dt in ('UDINT', 'UINT32'):
                typed.uint32Value = int(value)
            elif dt in ('LREAL', 'DOUBLE', 'FLOAT64'):
                typed.doubleValue = float(value)
            elif dt in ('REAL', 'FLOAT'):
                typed.floatValue = float(value)
            elif dt in ('STRING', 'WSTRING'):
                typed.stringValue = str(value)
            else:
                # Auto-detect
                if isinstance(value, bool):
                    typed.boolValue = value
                elif isinstance(value, int):
                    typed.int32Value = value
                elif isinstance(value, float):
                    typed.doubleValue = value
                else:
                    typed.stringValue = str(value)

            return typed
        except Exception as e:
            logger.error(f"Type conversion error: {e}")
            raise

    def update_sim(self, port_name: str, value):
        """Update simulation data (for testing)"""
        self._sim_data[port_name] = value

    def get_sim_data(self) -> dict:
        """Get all simulation data"""
        return dict(self._sim_data)