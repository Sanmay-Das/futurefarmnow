import numpy as np
import pandas as pd
import geopandas as gpd

from sklearn.preprocessing import StandardScaler, RobustScaler, MinMaxScaler, PowerTransformer
from sklearn.decomposition import PCA
from sklearn.neighbors import KNeighborsClassifier
from sklearn.covariance import EllipticEnvelope

from scipy.stats import trim_mean, trimboth, chi2
from scipy.spatial import KDTree
from scipy.spatial import distance, distance_matrix
from scipy.spatial.distance import mahalanobis

from shapely.geometry import Point
from pyDOE3 import ccdesign
from pykrige.ok import OrdinaryKriging
import pysal.lib
from esda.moran import Moran, Moran_BV_matrix
from libpysal.weights import DistanceBand, KNN

import random
from tqdm.auto import tqdm
from itertools import combinations
from functools import lru_cache
import itertools
from extract_points import *

def IQR_outliers(PCs, _threshold):
    """
    Filters out rows from the input array `PCs` that contain outliers.

    Parameters:
    - PCs: A 2D numpy array where each row represents a sample and each column represents a feature.
    - _threshold: A multiplier for the IQR to determine outlier bounds. Default is 1.5.

    Returns:
    - A boolean array where True indicates rows that are within the outlier bounds.
    """
    # Calculate Q1 and Q3 for each feature
    Q1, Q3 = np.percentile(PCs, [25, 75], axis=0)

    # Calculate the IQR for each feature
    IQR = Q3 - Q1

    # Determine lower and upper bounds for outliers
    lower_bounds = (Q1 - _threshold * IQR).reshape(1, -1)
    upper_bounds = (Q3 + _threshold * IQR).reshape(1, -1)

    # Determine which rows are within the bounds
    within_bounds = (PCs >= lower_bounds) & (PCs <= upper_bounds)

    # Return a boolean array where True means the row is within bounds
    return np.all(within_bounds, axis=1)


def mahalanobis_outliers(PCs, confidence_level):
    """
    Detects outliers in the input array `PCs` using the Mahalanobis distance.

    Parameters:
    - PCs: A 2D numpy array where each row represents a sample and each column represents a feature.
    - confidence_level: The confidence level for the chi-squared distribution to determine the outlier threshold.

    Returns:
    - A boolean array where True indicates rows that are considered outliers.
    """
    # Calculate the degrees of freedom (number of features)
    df = PCs.shape[1]

    # Calculate the threshold based on the chi-squared distribution
    threshold_pca = chi2.ppf(confidence_level, df=df)

    # Calculate the covariance matrix and its inverse
    cov_matrix = np.cov(PCs, rowvar=False)
    inv_cov_matrix = np.linalg.inv(cov_matrix)

    # Calculate the mean of the data
    mean_PCs = np.mean(PCs, axis=0)

    # Calculate Mahalanobis distances for all samples in one step
    # m_distances = [mahalanobis(x, mean_PCs, inv_cov_matrix) for x in PCs]
    # faster way by vectorizing
    centered_PCs = PCs - mean_PCs
    m_distances = np.sqrt(np.sum(centered_PCs @ inv_cov_matrix * centered_PCs, axis=1))

    # Return a boolean array where True means the Mahalanobis distance exceeds the threshold
    return m_distances <= threshold_pca

def elliptic_envelope_outliers(PCs, contamination_rate):
    """
    Detects outliers in the input array `PCs` using the Elliptic Envelope method.

    Parameters:
    - PCs: A 2D numpy array where each row represents a sample and each column represents a feature.
    - contamination_rate: The proportion of outliers in the data. Default is 0.1.

    Returns:
    - A boolean array where True indicates rows that are not considered outliers.
    """
    # Initialize the EllipticEnvelope model with the specified contamination rate
    elliptic_envelope_pca = EllipticEnvelope(contamination=contamination_rate)

    # Fit the model to the data and predict outliers
    y_pred_pca = elliptic_envelope_pca.fit_predict(PCs)

    # Return a boolean array where True means the row is not an outlier
    return y_pred_pca != -1

def generate_design(data, n_samples, whitten=0):
    ccd = ccdesign(2, center=(1,1), alpha='o', face='cci')
    scaled_ccd = np.zeros_like(ccd) # Initialize scaled CCD with the correct shape
    ccd_min, ccd_max = ccd.min(axis=0), ccd.max(axis=0) # Compute scaling parameters
    data_min, data_max = np.percentile(data, [whitten, 100-whitten], axis=0)
    scaled_ccd = (ccd - ccd_min) / (ccd_max - ccd_min) * (data_max - data_min) + data_min # Scale design
    # Extract subsets
    ccd_boxes = scaled_ccd[:4]
    ccd_star = scaled_ccd[5:9]
    support_points = lambda x: np.repeat(scaled_ccd[np.newaxis, 4, :], x, axis=0) # Support points generator
    # Define the designs list with optimized operations
    designs = [
        np.vstack([ccd_boxes, support_points(1)]),
        np.vstack([ccd_star, support_points(1)]),
    ]
    # Additional designs based on manipulation of existing ones
    designs += [designs[1][:2] / 2, designs[1][2:] / 2, designs[0] / 2]
    # Determine the design closest to the target number of samples
    al_list = np.cumsum([len(d) for d in designs])
    k = np.searchsorted(al_list, n_samples)
    # Concatenate the selected designs up to the closest match
    return np.vstack(designs[:k+1]), al_list.tolist()

def iter_combinations(num_combs=np.nan, filtered_distances = None, filtered_indices = None):

    dists = filtered_distances
    idxs = filtered_indices

    total_combs = np.prod([len(row) if len(row) else 1 for row in dists])
    print(f"Total possible combinations are {total_combs}")
    num_combs = int(np.nanmin([num_combs, total_combs]))
    print(f"iterating through {num_combs} of them")


    # Generate unique combinations efficiently
    combinations = set()

    if num_combs < 4600000:
        # Generate all possible combinations systematically
        all_combinations = itertools.product(*[itertools.product(row_dist, row_idx) for row_dist, row_idx in zip(dists, idxs)])
        for comb in tqdm(itertools.islice(all_combinations, num_combs)):
            curr_comb_dist, curr_comb_idx = zip(*comb)
            combinations.add((tuple(curr_comb_dist), tuple(curr_comb_idx)))
        return combinations

    for _ in tqdm(range(num_combs)):
        curr_comb_dist = []
        curr_comb_idx = []
        for row_dist, row_idx in zip(dists, idxs):
            if len(row_dist) > 0:
                chosen = random.choice(list(zip(row_dist, row_idx)))
                curr_comb_dist.append(chosen[0])
                curr_comb_idx.append(chosen[1])
            else:
                curr_comb_dist.append(None)
                curr_comb_idx.append(None)
        combinations.add((tuple(curr_comb_dist), tuple(curr_comb_idx)))

    return combinations

#outlier technique is important, so is scaler, we need a way to analyze scatter plot distribution so the scaler and outlier don't need to be chosen by the user
#scalar_scheme can be: StandardScaler, RobustScaler, PowerTransformer
#outlier tecnhique can be: IQR Thresholding, Mahalanobis Distance, Elliptic Envelope

def select_points(df, num_samples=10, epsg_code=32618, scalar_scheme='StandardScaler', 
                 outlier_technique='IQR Thresholding', weight=0.5, Morgans=False, 
                 output_name='results'):
    # Extract coordinate columns
    lat = df.columns[0]
    lon = df.columns[1]
    
    # Select feature columns
    selected_df = df.drop(columns=[lat, lon])
    
    # Create GeoDataFrame
    geometry = [Point(xy) for xy in df.loc[:,[lat, lon]].values]
    gdf = gpd.GeoDataFrame(selected_df, geometry=geometry)
    gdf.crs = f"EPSG:{epsg_code}"

    # Apply scaling
    scaler = {
        'RobustScaler': RobustScaler(),
        'StandardScaler': StandardScaler(),
        'PowerTransformer': PowerTransformer()
    }[scalar_scheme]
    
    X_scaled = scaler.fit_transform(selected_df.values)
    
    # Apply PCA and outlier detection
    pca = PCA(n_components=2)
    PCs = pca.fit_transform(X_scaled)
    
    rows_to_keep = np.ones(PCs.shape[0], dtype=bool)
    threshold = 0.5
    
    if outlier_technique == 'IQR Thresholding':
        rows_to_keep = IQR_outliers(PCs, threshold)
    elif outlier_technique == 'Mahalanobis Distance':
        if threshold > 1: threshold = 1
        rows_to_keep = mahalanobis_outliers(PCs, threshold)
    elif outlier_technique == 'Elliptic Envelope':
        if threshold > 0.5: threshold = 0.5
        rows_to_keep = elliptic_envelope_outliers(PCs, threshold)
    
    filtered_Pcs = PCs[rows_to_keep]
    
    # Generate design
    design, _ = generate_design(filtered_Pcs, num_samples, whitten=5)
    
    # Reduce design to exact requested sample count
    if len(design) > num_samples:
        dist_matrix = pairwise_distances(design)
        centrality = np.sum(dist_matrix, axis=1)
        most_central_indices = np.argsort(centrality)[:num_samples]
        design = design[most_central_indices]
    elif len(design) < num_samples:
        num_samples = len(design)
    
    # Create spatial and feature arrays
    Geo_space_XY = df.loc[rows_to_keep, [lat, lon]].values
    Var_space_XY = filtered_Pcs
    
    # Find nearest neighbors in feature space
    tree = KDTree(Var_space_XY)
    NNearest_neighbour = min(5, len(Var_space_XY))
    var_max = 0.4
    distances, indices = tree.query(design, k=NNearest_neighbour)
    
    # Filter candidates within allowed distance
    valid_indices = distances < var_max
    filtered_indices = [indices[i][valid_indices[i]] for i in range(len(design))]
    filtered_distances = [distances[i][valid_indices[i]] for i in range(len(design))]
    
    for i in range(len(design)):
        if len(filtered_indices[i]) == 0:
            dist_to_design = np.linalg.norm(Var_space_XY - design[i], axis=1)
            closest_idx = np.argmin(dist_to_design)
            filtered_indices[i] = [closest_idx]
            filtered_distances[i] = [dist_to_design[closest_idx]]
            print(f"\x1b[33mWarning: No candidates for design point {i}, using closest point {closest_idx}\x1b[0m")
    
    assigned_to = {}
    for i in range(len(design)):
        for idx, dist in zip(filtered_indices[i], filtered_distances[i]):
            if idx not in assigned_to:
                assigned_to[idx] = (i, dist)
            else:
                _, prev_dist = assigned_to[idx]
                if dist < prev_dist:
                    assigned_to[idx] = (i, dist)
    
    # Rebuild candidate lists with unique assignments
    assigned_indices = [[] for _ in range(len(design))]
    for idx, (i, _) in assigned_to.items():
        assigned_indices[i].append(idx)
    
    # Ensure each design point has at least one candidate
    for i in range(len(assigned_indices)):
        if len(assigned_indices[i]) == 0:
            # Find the closest unassigned point
            unassigned = [idx for idx in range(len(Var_space_XY)) if idx not in assigned_to]
            if unassigned:
                dist_to_design = np.linalg.norm(Var_space_XY[unassigned] - design[i], axis=1)
                closest_idx = unassigned[np.argmin(dist_to_design)]
                assigned_indices[i] = [closest_idx]
                assigned_to[closest_idx] = (i, np.min(dist_to_design))
                print(f"\x1b[33mWarning: Added fallback point {closest_idx} for design point {i}\x1b[0m")
    
    filtered_indices = [np.array(lst) for lst in assigned_indices]
    
    epsilion = 1e-7
    geo_dist_matrix = distance_matrix(Geo_space_XY, Geo_space_XY)
    np.fill_diagonal(geo_dist_matrix, np.inf)
    geo_max = np.nanmax(geo_dist_matrix)
    geo_min = np.nanmin(geo_dist_matrix) + epsilion
    var_min = 0 + epsilion
    
    scale_geo = lambda x: (x - geo_min)/(geo_max - geo_min)*3
    scale_var = lambda x: (x - var_min)/(var_max - var_min)*3
    
    # Generate all possible candidate combinations
    candidate_sets = []
    for i in range(len(design)):
        candidate_sets.append(filtered_indices[i])
    
    # Generate combinations ensuring one candidate per design point
    all_combinations = list(itertools.product(*candidate_sets))
    
    # Filter for distinct points only
    distinct_combinations = []
    for combo in all_combinations:
        if len(set(combo)) == num_samples:
            distinct_combinations.append(combo)
    
    # Score combinations
    best_score = float('-inf')
    best_combo = None
    
    for combo in tqdm(distinct_combinations, desc="Scoring combinations"):
        point_indices = np.array(combo)
        
        # Calculate geographic spread (minimum distance between any two points)
        geo_points = Geo_space_XY[point_indices]
        geo_dist = distance_matrix(geo_points, geo_points)
        np.fill_diagonal(geo_dist, np.inf)
        min_geo_dist = np.min(geo_dist)
        
        # Calculate feature representation (maximum distance to design points)
        max_feature_dist = 0
        for i, point_idx in enumerate(point_indices):
            # Find distance from this point to its design point
            point_feature = Var_space_XY[point_idx]
            dist_to_design = np.linalg.norm(point_feature - design[i])
            if dist_to_design > max_feature_dist:
                max_feature_dist = dist_to_design
        
        # Apply scaling
        min_geo_dist_scaled = scale_geo(min_geo_dist)
        max_feature_dist_scaled = scale_var(max_feature_dist)
        
        # Calculate combined score
        score = (1 - weight) * min_geo_dist_scaled - weight * max_feature_dist_scaled
        
        if score > best_score:
            best_score = score
            best_combo = point_indices
    
    # Final result handling
    if best_combo is not None:
        final_indices = best_combo
    else:
        # Fallback: select first candidate for each design point
        final_indices = [candidates[0] for candidates in candidate_sets]
        print("\x1b[33mWarning: Using fallback candidate selection\x1b[0m")
    
    # Ensure we have exactly the requested number of points
    final_indices = final_indices[:num_samples]
    
    # Create output with actual points from the dataset
    result_points = Geo_space_XY[final_indices]
    ndf = pd.DataFrame(result_points, columns=[lat, lon])
    ndf.to_csv(f"{output_name}.csv", index=None)
    
    return ndf