#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MotoPick_iCube Web Interface
Flask + gRPC backend for MotoPick Pick & Place system management
"""
import sys
import os
import shutil
import subprocess
import glob
import json
import time
import signal
import logging
from datetime import datetime
from collections import deque
from threading import Lock

# ==================== LIBRARY SETUP ====================
app_dir = os.path.dirname(os.path.abspath(__file__))
source_libs = os.path.join(app_dir, 'pylibs')           
target_libs = '/opt/plcnext/user/data/grpc_libs_v1'     

def setup_libraries():
    """Copia le librerie e corregge il bug del nome cygrpc"""
    if not os.path.exists(target_libs):
        print(f"[SETUP] Primo avvio: Installazione librerie in {target_libs}...")
        try:
            shutil.copytree(source_libs, target_libs)
            subprocess.call(['chmod', '-R', '755', target_libs])
            
            # FIX CRITICO: Rinomina cygrpc.cpython-XXX.so -> cygrpc.so
            cython_path = os.path.join(target_libs, 'grpc', '_cython')
            so_files = glob.glob(os.path.join(cython_path, "cygrpc*.so"))
            
            for f in so_files:
                filename = os.path.basename(f)
                if filename != "cygrpc.so":
                    new_name = os.path.join(cython_path, "cygrpc.so")
                    print(f"[SETUP] Rinomina file critico: {filename} -> cygrpc.so")
                    os.rename(f, new_name)
            
            print("[SETUP] Installazione completata con successo.")
        except Exception as e:
            print(f"[ERRORE] Setup fallito: {e}")
            return source_libs
    return target_libs

final_libs_path = target_libs
if os.path.exists(source_libs):
    final_libs_path = setup_libraries()

if final_libs_path not in sys.path:
    sys.path.insert(0, final_libs_path)
# ==================== END LIBRARY SETUP ====================

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from grpc_client import GrpcClient

# ==================== LOGGING ====================
def _get_log_handlers():
    """Build logging handlers, falling back to stdout-only if file is not writable."""
    handlers = [logging.StreamHandler(sys.stdout)]

    # Try candidate directories in order of preference
    for candidate in [
        '/opt/plcnext/logs/grpc_webserver',       # same dir as AxisControlPanel (already has perms)
        os.path.join(app_dir, 'logs'),             # next to main.py
        '/tmp/motopick_webserver',
    ]:
        try:
            os.makedirs(candidate, exist_ok=True)
            log_path = os.path.join(candidate, 'motopick.log')
            fh = logging.FileHandler(log_path)
            handlers.insert(0, fh)
            print(f"[LOGGING] Log file: {log_path}", flush=True)
            break
        except (OSError, PermissionError):
            continue
    else:
        print("[LOGGING] Could not open any log file – logging to stdout only.", flush=True)

    return handlers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_get_log_handlers()
)
logger = logging.getLogger(__name__)

# ==================== FLASK APP ====================
app = Flask(__name__)
CORS(app)

grpc_client = None

# ==================== STATE ====================
event_log = deque(maxlen=500)
event_log_lock = Lock()

# Default project data structure
DEFAULT_PROJECT = {
    "name": "New Project",
    "robots": [],
    "feeds": [],
    "supplies": [],
    "grippers": [],
    "products": [],
    "formats": [],
    "grip_rules": [],
    "work_areas": [],
    "load_share": [],
    "pick_patterns": [],
    "place_patterns": [],
    "item_sources": [],
    "item_order": {},
    "robot_motion": [],
    "layout": {
        "components": []
    }
}

# In-memory project store (would be file-based in production)
current_project = {}
project_lock = Lock()

# ==================== DATA DIR ====================
# /opt/plcnext/apps/ is read-only on PLCnext. Use a writable path.
def _find_writable_dir():
    candidates = [
        '/opt/plcnext/user/data/motopick',  # persists across reboots
        '/opt/plcnext/user/motopick',
        '/tmp/motopick_data',               # lost on reboot, but always writable
    ]
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            test = os.path.join(path, '.write_test')
            with open(test, 'w') as f:
                f.write('ok')
            os.remove(test)
            print(f'[DATA] Using data directory: {path}', flush=True)
            return path
        except (OSError, PermissionError):
            continue
    raise RuntimeError('No writable data directory found on this system')

data_dir = _find_writable_dir()
PROJECT_FILE = os.path.join(data_dir, 'project.json')

def load_project_from_disk():
    global current_project
    if os.path.exists(PROJECT_FILE):
        try:
            with open(PROJECT_FILE, 'r', encoding='utf-8') as f:
                current_project = json.load(f)
            logger.info(f"Project loaded from {PROJECT_FILE}")
        except Exception as e:
            logger.error(f"Failed to load project: {e}")
            current_project = dict(DEFAULT_PROJECT)
    else:
        current_project = dict(DEFAULT_PROJECT)
        # Add demo data
        _init_demo_project()

def save_project_to_disk():
    try:
        with project_lock:
            with open(PROJECT_FILE, 'w', encoding='utf-8') as f:
                json.dump(current_project, f, indent=2, ensure_ascii=False)
        logger.info("Project saved to disk")
        return True
    except Exception as e:
        logger.error(f"Failed to save project: {e}")
        return False

def _init_demo_project():
    """Initialize with demo data matching the screenshots"""
    global current_project
    current_project = {
        "name": "Demo Project",
        "robots": [
            {
                "id": 1, "name": "Robot 01", "ip": "192.168.0.1",
                "gripper": "Single Gripper", "controller": "FS100",
                "feeds": ["Pick Conveyor", "Place Conveyor", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
                "supplies": ["Camera", "Pattern Host", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
                "enabled": True
            },
            {
                "id": 2, "name": "Robot 02", "ip": "192.168.0.2",
                "gripper": "Single Gripper", "controller": "FS100",
                "feeds": ["Pick Conveyor", "Place Conveyor", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
                "supplies": ["Camera", "Pattern Host", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
                "enabled": True
            }
        ],
        "feeds": [
            {
                "id": 1, "name": "Pick Conveyor", "type": "Pick Conveyor",
                "item_source": "Camera",
                "generate_batches_auto": True,
                "max_batch_distance": 300.0,
                "min_batch_distance": 0.0,
                "image_trigger_delay": 0.0,
                "ref_offset_x": 0.0, "ref_offset_y": 0.0, "ref_angle": 0.0,
                "filter_below_x": None, "filter_above_x": None,
                "filter_below_y": None, "filter_above_y": None,
                "robot_offsets": [
                    {"robot": "Robot 01", "slot": 1, "offset_x": 400.0, "offset_y": 0.0},
                    {"robot": "Robot 02", "slot": 1, "offset_x": 800.0, "offset_y": 0.0}
                ]
            },
            {
                "id": 2, "name": "Place Conveyor", "type": "Place Conveyor",
                "item_source": "Pattern Host",
                "generate_batches_auto": False,
                "max_batch_distance": 0.0,
                "min_batch_distance": 0.0,
                "image_trigger_delay": 0.0,
                "ref_offset_x": 0.0, "ref_offset_y": 0.0, "ref_angle": 0.0,
                "filter_below_x": None, "filter_above_x": None,
                "filter_below_y": None, "filter_above_y": None,
                "robot_offsets": []
            }
        ],
        "supplies": [
            {
                "id": 1, "name": "Camera", "type": "Pick Vision System",
                "ip": "192.168.0.254",
                "vision_driver": "Cognex (Basic)",
                "driver_name": "COGNEX", "driver_port": 23,
                "master_robots": ["Robot 01"]
            },
            {
                "id": 2, "name": "Pattern Host", "type": "Pattern Host",
                "ip": "", "vision_driver": "", "driver_name": "", "driver_port": 0,
                "master_robots": []
            }
        ],
        "grippers": [
            {
                "id": 1, "name": "Single Gripper",
                "verify_picking": False, "verify_placing": False,
                "tcps": [
                    {"id": i, "mode": "Tool", "group": False,
                     "tools": [False]*8}
                    for i in range(1, 17)
                ]
            }
        ],
        "products": [
            {
                "id": 1, "name": "Cherry", "color": "#CC0000",
                "tolerance_x": 5.0, "tolerance_y": 5.0, "min_score": 80.0,
                "supported_tcps": {"Single Gripper": [True] + [False]*15},
                "enabled": True
            },
            {
                "id": 2, "name": "Grape", "color": "#00AA00",
                "tolerance_x": 5.0, "tolerance_y": 5.0, "min_score": 80.0,
                "supported_tcps": {"Single Gripper": [True] + [False]*15},
                "enabled": True
            }
        ],
        "formats": [
            {"id": 1, "name": "Pure Layers", "template": None},
            {"id": 2, "name": "Mixed Layers", "template": None},
        ] + [{"id": i, "name": "", "template": None} for i in range(3, 201)],
        "grip_rules": [
            {
                "id": 1, "active_on_startup": True,
                "tools": [
                    {
                        "id": 1,
                        "allowed_types": ["Cherry", "Grape"],
                        "min_weight": None, "max_weight": None,
                        "pick_level": "Level 1", "place_level": "Level 1"
                    }
                ]
            }
        ] + [{"id": i, "active_on_startup": False, "tools": []} for i in range(2, 65)],
        "work_areas": [
            {
                "robot_id": 1,
                "feeds": [
                    {
                        "feed": "Pick Conveyor", "enabled": True,
                        "min_x": 250.0, "max_x": 550.0,
                        "min_y": None, "max_y": None,
                        "slow_mm": None, "stop_mm": None
                    },
                    {
                        "feed": "Place Conveyor", "enabled": True,
                        "min_x": 650.0, "max_x": 850.0,
                        "min_y": None, "max_y": None,
                        "slow_mm": None, "stop_mm": 800.0
                    }
                ]
            },
            {
                "robot_id": 2,
                "feeds": [
                    {
                        "feed": "Pick Conveyor", "enabled": True,
                        "min_x": 250.0, "max_x": 550.0,
                        "min_y": None, "max_y": None,
                        "slow_mm": None, "stop_mm": None
                    },
                    {
                        "feed": "Place Conveyor", "enabled": True,
                        "min_x": 650.0, "max_x": 850.0,
                        "min_y": None, "max_y": None,
                        "slow_mm": None, "stop_mm": 800.0
                    }
                ]
            }
        ],
        "load_share": [
            {
                "affected_types": ["Cherry"],
                "strategy": "Balanced",
                "min_y": None, "max_y": None,
                "ratios": {"Robot 01": 1, "Robot 02": 1}
            },
            {
                "affected_types": ["Cherry", "Grape"],
                "strategy": "Balanced",
                "min_y": None, "max_y": None,
                "ratios": {"Robot 01": 1, "Robot 02": 1}
            }
        ],
        "pick_patterns": [],
        "place_patterns": [
            {
                "id": 1, "name": "Place Pattern",
                "items": [
                    {
                        "allowed_types": ["Cherry", "Grape"],
                        "robots": ["Robot 01", "Robot 02"],
                        "pos_x": 0.0, "pos_y": -50.0, "pos_z": 0.0,
                        "rot_x": 0.0, "rot_y": 0.0, "rot_z": 0.0,
                        "layer": "Layer 1",
                        "min_weight": None, "max_weight": None
                    },
                    {
                        "allowed_types": ["Cherry", "Grape"],
                        "robots": ["Robot 01", "Robot 02"],
                        "pos_x": 0.0, "pos_y": 50.0, "pos_z": 0.0,
                        "rot_x": 0.0, "rot_y": 0.0, "rot_z": 0.0,
                        "layer": "Layer 1",
                        "min_weight": None, "max_weight": None
                    }
                ]
            }
        ],
        "item_sources": [
            {"id": 1, "pattern_host": "Pattern Host", "pattern": "Place Pattern", "vision_job_camera": "Camera", "vision_job": "Fruits.job"}
        ],
        "item_order": {
            "selection_mode": "Match in Place Order",
            "match_strict_order": False,
            "switch_feeds_picking": False,
            "switch_feeds_placing": False,
            "pick_item_order": {"x": "Descending", "y": "Descending", "z": "Descending", "batch": "Ascending", "feed": "Ascending", "type": "Ascending"},
            "pick_feed_order": ["Pick Conveyor"],
            "pick_type_order": ["Cherry", "Grape"],
            "place_item_order": {"x": "Descending", "y": "Descending", "z": "Descending", "batch": "Ascending", "feed": "Ascending", "type": "Ascending"},
            "place_feed_order": ["Place Conveyor"],
            "place_type_order": ["Cherry", "Grape"]
        },
        "robot_motion": [
            {
                "robot_id": 1,
                "feeds": [
                    {
                        "feed": "Pick Conveyor",
                        "products": [
                            {
                                "product": "Cherry",
                                "approach_pos": [0.0, 0.0, 150.0],
                                "approach_vel": 3000.0, "approach_precision": "Regular",
                                "processing_pos": [0.0, 0.0, 50.0],
                                "processing_vel": 3000.0, "processing_precision": "Position Level",
                                "processing_level": 1,
                                "escape_pos": [0.0, 0.0, 150.0],
                                "escape_vel": 3000.0, "escape_precision": "Regular",
                                "duration": 0.8, "advance": 0.2
                            },
                            {
                                "product": "Grape",
                                "approach_pos": [0.0, 0.0, 150.0],
                                "approach_vel": 3000.0, "approach_precision": "Regular",
                                "processing_pos": [0.0, 0.0, 40.0],
                                "processing_vel": 3000.0, "processing_precision": "Position Level",
                                "processing_level": 1,
                                "escape_pos": [0.0, 0.0, 150.0],
                                "escape_vel": 3000.0, "escape_precision": "Regular",
                                "duration": 1.0, "advance": 0.25
                            }
                        ]
                    }
                ]
            }
        ],
        "layout": {
            "components": [
                {"id": "camera1", "type": "camera", "label": "Camera", "x": 100, "y": 150, "width": 60, "height": 60, "angle": 0},
                {"id": "pick_conv", "type": "conveyor", "label": "Pick Conveyor", "x": 170, "y": 155, "width": 350, "height": 50, "angle": 0, "conveyor_type": "pick"},
                {"id": "robot1", "type": "robot", "label": "Robot 01", "x": 230, "y": 230, "width": 50, "height": 50, "angle": 0},
                {"id": "robot2", "type": "robot", "label": "Robot 02", "x": 430, "y": 230, "width": 50, "height": 50, "angle": 0},
                {"id": "place_conv", "type": "conveyor", "label": "Place Conveyor", "x": 100, "y": 300, "width": 350, "height": 50, "angle": 0, "conveyor_type": "place"},
                {"id": "pattern1", "type": "pattern_host", "label": "Pattern Host", "x": 460, "y": 305, "width": 60, "height": 50, "angle": 0}
            ]
        }
    }

# ==================== gRPC INIT ====================
def init_grpc_client():
    global grpc_client
    try:
        grpc_address = os.getenv('GRPC_ADDRESS', 'unix:///run/plcnext/grpc.sock')
        grpc_client = GrpcClient(grpc_address)
        logger.info(f"gRPC client initialized: {grpc_address}")
        return True
    except Exception as e:
        logger.error(f"gRPC init failed: {e}")
        return False

def add_event(message: str, level: str = "INFO"):
    with event_log_lock:
        event_log.append({
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message
        })

def graceful_shutdown(signum, frame):
    logger.info(f"Shutdown signal {signum}")
    save_project_to_disk()
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)

# ==================== API ROUTES ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    sim_mode = grpc_client is None or not grpc_client.is_connected
    return jsonify({
        "status": "ok",
        "grpc": grpc_client is not None,
        "simulation": sim_mode,
        "timestamp": datetime.now().isoformat()
    }), 200

# --- PROJECT ---

@app.route('/api/project', methods=['GET'])
def get_project():
    with project_lock:
        return jsonify({"project": current_project, "success": True}), 200

@app.route('/api/project', methods=['POST'])
def update_project():
    data = request.get_json(silent=True) or {}
    with project_lock:
        # Accept full project replacement or partial update
        for key in DEFAULT_PROJECT:
            if key in data:
                current_project[key] = data[key]
    save_project_to_disk()
    return jsonify({"success": True}), 200

@app.route('/api/project/save', methods=['POST'])
def save_project():
    ok = save_project_to_disk()
    add_event("Project saved", "INFO")
    return jsonify({"success": ok}), 200 if ok else 500

# --- LAYOUT ---

@app.route('/api/layout', methods=['GET'])
def get_layout():
    with project_lock:
        return jsonify({"layout": current_project.get("layout", {"components": []}), "success": True}), 200

@app.route('/api/layout', methods=['POST'])
def update_layout():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['layout'] = data.get('layout', current_project.get('layout', {"components": []}))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- ROBOTS ---

@app.route('/api/robots', methods=['GET'])
def get_robots():
    with project_lock:
        return jsonify({"robots": current_project.get("robots", []), "success": True}), 200

@app.route('/api/robots', methods=['POST'])
def update_robots():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['robots'] = data.get('robots', current_project.get('robots', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

@app.route('/api/robots/<int:robot_id>', methods=['PUT'])
def update_robot(robot_id):
    data = request.get_json(silent=True) or {}
    with project_lock:
        robots = current_project.get('robots', [])
        for i, r in enumerate(robots):
            if r.get('id') == robot_id:
                robots[i].update(data)
                break
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- FEEDS ---

@app.route('/api/feeds', methods=['GET'])
def get_feeds():
    with project_lock:
        return jsonify({"feeds": current_project.get("feeds", []), "success": True}), 200

@app.route('/api/feeds', methods=['POST'])
def update_feeds():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['feeds'] = data.get('feeds', current_project.get('feeds', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

@app.route('/api/feeds/<int:feed_id>', methods=['PUT'])
def update_feed(feed_id):
    data = request.get_json(silent=True) or {}
    with project_lock:
        feeds = current_project.get('feeds', [])
        for i, f in enumerate(feeds):
            if f.get('id') == feed_id:
                feeds[i].update(data)
                break
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- SUPPLIES ---

@app.route('/api/supplies', methods=['GET'])
def get_supplies():
    with project_lock:
        return jsonify({"supplies": current_project.get("supplies", []), "success": True}), 200

@app.route('/api/supplies', methods=['POST'])
def update_supplies():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['supplies'] = data.get('supplies', current_project.get('supplies', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

@app.route('/api/supplies/<int:supply_id>', methods=['PUT'])
def update_supply(supply_id):
    data = request.get_json(silent=True) or {}
    with project_lock:
        supplies = current_project.get('supplies', [])
        for i, s in enumerate(supplies):
            if s.get('id') == supply_id:
                supplies[i].update(data)
                break
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- GRIPPERS ---

@app.route('/api/grippers', methods=['GET'])
def get_grippers():
    with project_lock:
        return jsonify({"grippers": current_project.get("grippers", []), "success": True}), 200

@app.route('/api/grippers', methods=['POST'])
def update_grippers():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['grippers'] = data.get('grippers', current_project.get('grippers', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- PRODUCTS ---

@app.route('/api/products', methods=['GET'])
def get_products():
    with project_lock:
        return jsonify({"products": current_project.get("products", []), "success": True}), 200

@app.route('/api/products', methods=['POST'])
def update_products():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['products'] = data.get('products', current_project.get('products', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- FORMATS ---

@app.route('/api/formats', methods=['GET'])
def get_formats():
    with project_lock:
        return jsonify({"formats": current_project.get("formats", []), "success": True}), 200

@app.route('/api/formats', methods=['POST'])
def update_formats():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['formats'] = data.get('formats', current_project.get('formats', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- GRIP RULES ---

@app.route('/api/grip_rules', methods=['GET'])
def get_grip_rules():
    with project_lock:
        return jsonify({"grip_rules": current_project.get("grip_rules", []), "success": True}), 200

@app.route('/api/grip_rules', methods=['POST'])
def update_grip_rules():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['grip_rules'] = data.get('grip_rules', current_project.get('grip_rules', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- WORK AREAS ---

@app.route('/api/work_areas', methods=['GET'])
def get_work_areas():
    with project_lock:
        return jsonify({"work_areas": current_project.get("work_areas", []), "success": True}), 200

@app.route('/api/work_areas', methods=['POST'])
def update_work_areas():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['work_areas'] = data.get('work_areas', current_project.get('work_areas', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- LOAD SHARE ---

@app.route('/api/load_share', methods=['GET'])
def get_load_share():
    with project_lock:
        return jsonify({"load_share": current_project.get("load_share", []), "success": True}), 200

@app.route('/api/load_share', methods=['POST'])
def update_load_share():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['load_share'] = data.get('load_share', current_project.get('load_share', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- PICK PATTERNS ---

@app.route('/api/pick_patterns', methods=['GET'])
def get_pick_patterns():
    with project_lock:
        return jsonify({"pick_patterns": current_project.get("pick_patterns", []), "success": True}), 200

@app.route('/api/pick_patterns', methods=['POST'])
def update_pick_patterns():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['pick_patterns'] = data.get('pick_patterns', current_project.get('pick_patterns', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- PLACE PATTERNS ---

@app.route('/api/place_patterns', methods=['GET'])
def get_place_patterns():
    with project_lock:
        return jsonify({"place_patterns": current_project.get("place_patterns", []), "success": True}), 200

@app.route('/api/place_patterns', methods=['POST'])
def update_place_patterns():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['place_patterns'] = data.get('place_patterns', current_project.get('place_patterns', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- ITEM SOURCES ---

@app.route('/api/item_sources', methods=['GET'])
def get_item_sources():
    with project_lock:
        return jsonify({"item_sources": current_project.get("item_sources", []), "success": True}), 200

@app.route('/api/item_sources', methods=['POST'])
def update_item_sources():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['item_sources'] = data.get('item_sources', current_project.get('item_sources', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- ITEM ORDER ---

@app.route('/api/item_order', methods=['GET'])
def get_item_order():
    with project_lock:
        return jsonify({"item_order": current_project.get("item_order", {}), "success": True}), 200

@app.route('/api/item_order', methods=['POST'])
def update_item_order():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['item_order'] = data.get('item_order', current_project.get('item_order', {}))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- ROBOT MOTION ---

@app.route('/api/robot_motion', methods=['GET'])
def get_robot_motion():
    with project_lock:
        return jsonify({"robot_motion": current_project.get("robot_motion", []), "success": True}), 200

@app.route('/api/robot_motion', methods=['POST'])
def update_robot_motion():
    data = request.get_json(silent=True) or {}
    with project_lock:
        current_project['robot_motion'] = data.get('robot_motion', current_project.get('robot_motion', []))
    save_project_to_disk()
    return jsonify({"success": True}), 200

# --- CONTROL ---

@app.route('/api/control/connect', methods=['POST'])
def control_connect():
    """Simulate connecting to MotoPick system"""
    data = request.get_json(silent=True) or {}
    ip = data.get('ip', '')
    add_event(f"Connecting to system at {ip}...", "INFO")
    # In real implementation: send connect command via gRPC
    add_event("Connection established", "INFO")
    return jsonify({"success": True, "message": "Connected"}), 200

@app.route('/api/control/launch', methods=['POST'])
def control_launch():
    """Launch the MotoPick system"""
    data = request.get_json(silent=True) or {}
    fmt = data.get('format', '')
    add_event(f"Launching system with format: {fmt}", "INFO")
    return jsonify({"success": True, "message": "System launched"}), 200

@app.route('/api/control/load', methods=['POST'])
def control_load():
    """Load format"""
    data = request.get_json(silent=True) or {}
    fmt = data.get('format', '')
    add_event(f"Loading format: {fmt}", "INFO")
    return jsonify({"success": True, "message": f"Format '{fmt}' loaded"}), 200

@app.route('/api/control/enable', methods=['POST'])
def control_enable():
    """Enable system"""
    add_event("System enabled", "INFO")
    return jsonify({"success": True, "message": "Enabled"}), 200

@app.route('/api/control/disconnect', methods=['POST'])
def control_disconnect():
    """Disconnect from MotoPick system"""
    add_event("Disconnected from system", "INFO")
    return jsonify({"success": True, "message": "Disconnected"}), 200

@app.route('/api/control/stop', methods=['POST'])
def control_stop():
    """Stop the MotoPick system"""
    add_event("System stopped", "WARNING")
    return jsonify({"success": True, "message": "System stopped"}), 200

@app.route('/api/control/transmit', methods=['POST'])
def control_transmit():
    """Transmit project to controller"""
    add_event("Project transmitted to controller", "INFO")
    return jsonify({"success": True, "message": "Transmitted"}), 200

@app.route('/api/control/status', methods=['GET'])
def control_status():
    """Get controller status"""
    sim_mode = grpc_client is None or not grpc_client.is_connected
    return jsonify({
        "success": True,
        "connected": not sim_mode,
        "running": False,
        "format_loaded": None,
        "simulation": sim_mode
    }), 200

# --- EVENTS ---

@app.route('/api/events', methods=['GET'])
def get_events():
    limit = request.args.get('limit', 100, type=int)
    with event_log_lock:
        events = list(event_log)[-limit:]
    return jsonify({"events": events, "success": True}), 200

@app.route('/api/events/clear', methods=['POST'])
def clear_events():
    with event_log_lock:
        event_log.clear()
    return jsonify({"success": True}), 200

# --- LIVE DATA (gRPC reads) ---

@app.route('/api/live/system', methods=['GET'])
def live_system():
    """Read live system status from PLC"""
    if grpc_client is None:
        return jsonify({"success": False, "error": "No gRPC connection"}), 503

    vars_to_read = [
        "Arp.Plc.Eclr/MotoPick.System.Running",
        "Arp.Plc.Eclr/MotoPick.System.Error",
        "Arp.Plc.Eclr/MotoPick.System.PicksPerMinute",
        "Arp.Plc.Eclr/MotoPick.System.TotalPicks",
        "Arp.Plc.Eclr/MotoPick.System.MissedItems",
    ]

    results = grpc_client.read_multiple(vars_to_read)
    data = {}
    for r in results:
        key = r['port_name'].split('.')[-1]
        data[key] = r.get('value')

    return jsonify({"system": data, "success": True, "simulated": not grpc_client.is_connected}), 200

@app.route('/api/live/robots', methods=['GET'])
def live_robots():
    """Read live robot status from PLC"""
    if grpc_client is None:
        return jsonify({"success": False, "error": "No gRPC connection"}), 503

    with project_lock:
        robot_count = len(current_project.get('robots', []))

    vars_to_read = []
    for i in range(1, robot_count + 1):
        prefix = f"Arp.Plc.Eclr/MotoPick.Robot{i:02d}"
        vars_to_read += [
            f"{prefix}.Running",
            f"{prefix}.Error",
            f"{prefix}.PicksPerMinute",
            f"{prefix}.TotalPicks",
        ]

    results = grpc_client.read_multiple(vars_to_read)

    robots_data = {}
    for r in results:
        parts = r['port_name'].split('.')
        robot_key = parts[-2] if len(parts) >= 2 else 'Unknown'
        field = parts[-1]
        if robot_key not in robots_data:
            robots_data[robot_key] = {}
        robots_data[robot_key][field] = r.get('value')

    return jsonify({"robots": robots_data, "success": True, "simulated": not grpc_client.is_connected}), 200

@app.route('/api/live/conveyors', methods=['GET'])
def live_conveyors():
    """Read live conveyor status from PLC"""
    if grpc_client is None:
        return jsonify({"success": False, "error": "No gRPC connection"}), 503

    with project_lock:
        feeds = current_project.get('feeds', [])

    conveyors_data = {}
    vars_to_read = []
    for i in range(1, len(feeds) + 1):
        prefix = f"Arp.Plc.Eclr/MotoPick.Conveyor{i:02d}"
        vars_to_read += [f"{prefix}.Running", f"{prefix}.Speed", f"{prefix}.ActualSpeed"]

    results = grpc_client.read_multiple(vars_to_read)
    for r in results:
        parts = r['port_name'].split('.')
        conv_key = parts[-2] if len(parts) >= 2 else 'Unknown'
        field = parts[-1]
        if conv_key not in conveyors_data:
            conveyors_data[conv_key] = {}
        conveyors_data[conv_key][field] = r.get('value')

    return jsonify({"conveyors": conveyors_data, "success": True, "simulated": not grpc_client.is_connected}), 200

# --- GENERIC gRPC ---

@app.route('/api/v1/read', methods=['POST'])
def read_variable():
    if grpc_client is None:
        return jsonify({"error": "No gRPC client", "success": False}), 503
    data = request.get_json(silent=True) or {}
    port_name = data.get('port_name')
    if not port_name:
        return jsonify({"error": "Missing port_name", "success": False}), 400
    result = grpc_client.read_single(port_name)
    return jsonify(result), 200

@app.route('/api/v1/write', methods=['POST'])
def write_variable():
    if grpc_client is None:
        return jsonify({"error": "No gRPC client", "success": False}), 503
    data = request.get_json(silent=True) or {}
    port_name = data.get('port_name')
    value = data.get('value')
    data_type = data.get('type', 'AUTO')
    if not port_name:
        return jsonify({"error": "Missing port_name", "success": False}), 400
    ok = grpc_client.write_single(port_name, value, data_type)
    return jsonify({"success": ok}), 200 if ok else 500

# --- SERVE HTML ---

@app.route('/')
def index():
    html_path = os.path.join(app_dir, 'templates', 'index.html')
    try:
        with open(html_path, 'r', encoding='utf-8') as fh:
            content = fh.read()
    except UnicodeDecodeError:
        with open(html_path, 'r', encoding='latin-1') as fh:
            content = fh.read()
    from flask import Response
    return Response(content, mimetype='text/html')

# ==================== MAIN ====================

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("MotoPick_iCube Web Interface Starting")
    logger.info("=" * 60)

    load_project_from_disk()
    add_event("System started", "INFO")

    if not init_grpc_client():
        logger.warning("gRPC init failed - running in simulation mode")
        add_event("Running in simulation mode (no gRPC)", "WARNING")

    port = int(os.getenv('WEBSERVER_PORT', 8080))
    logger.info(f"HTTP server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)