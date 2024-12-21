"""
File: soil.py

This script provides utility functions to query subdirectories of soil layers based on depth range.
It defines constants for the base directory and supported soil layers, and contains functions
to retrieve directories matching a specified depth range for a specific layer.

Constants:
- BASE_DIR: The base directory path where the soil layers are stored.
- SUPPORTED_LAYERS: A list of valid soil layers that can be queried.

Functions:

- get_matching_subdirectories(polaris_path, depth_range, layer):
    Given a depth range and a specific soil layer, this function returns a list of subdirectories
    within the layer's directory that match the specified depth range. It ensures the directory
    exists and checks if the subdirectory depth ranges overlap with the query range.
"""

import os  # For working with paths and directories
import sys  # For handling system-level operations (like file path errors)
from typing import List  # For type hints (if necessary)

# Directories and root path to soil layers
BASE_DIR = "/path/to/data/POLARIS"
BASE_DIR = "/Users/eldawy/IdeaProjects/futurefarmnow/data/POLARIS"

# Supported layers
SUPPORTED_LAYERS = [
    "alpha", "bd", "clay", "hb", "ksat", "lambda", "n", "om", "ph",
    "sand", "silt", "theta_r", "theta_s"
]

def get_matching_subdirectories(polaris_path, depth_range, layer):
    """
    Get subdirectories that match the depth range query for a specific layer.

    :param polaris_path: The base path to the POLARIS dataset.
    :param depth_range: The depth range to query, e.g., "0-60".
    :param layer: The specific layer to query, e.g., "alpha", "bd", etc.
    :return: A list of subdirectories that match the depth range query.
    """
    matching_dirs = []

    # Parse the input depth range
    try:
        from_depth, to_depth = map(int, depth_range.split('-'))
    except ValueError:
        raise ValueError(f"Invalid depth range format: {depth_range}. Expected format is 'from-to'.")

    # Path to the specific layer directory
    layer_dir = os.path.join(polaris_path, layer)

    # Ensure the layer directory exists
    if not os.path.exists(layer_dir):
        raise FileNotFoundError(f"Layer directory does not exist: {layer_dir}")

    # Iterate over the subdirectories in the layer directory
    for subdir in os.listdir(layer_dir):
        if subdir.endswith("_compressed"):
            # Extract the depth range from the subdirectory name, e.g., "0_5_compressed" -> 0 and 5
            depth_str = subdir.replace("_compressed", "")
            try:
                sub_from_depth, sub_to_depth = map(int, depth_str.split('_'))
            except ValueError:
                continue  # Skip subdirectories that do not match the expected format

            # Check if the subdirectory depth range overlaps with the input range
            if (sub_from_depth <= to_depth) and (sub_to_depth >= from_depth):
                # If there is overlap, add the full subdirectory path to the matching list
                matching_dirs.append(os.path.join(layer_dir, subdir))

    return matching_dirs
