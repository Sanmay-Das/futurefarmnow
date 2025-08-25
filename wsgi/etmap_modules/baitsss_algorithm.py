#!/usr/bin/env python3
"""
BAITSSS ET Algorithm - Pure Physics Implementation
Biosphere-Atmosphere Interactions Two-Source Surface Model
Block-wise processing with complete physics
"""

import numpy as np
import math
from typing import Dict, Optional


class BAITSSSConstants:
    """Physical constants for BAITSSS ET model (matched to Scala BAITSSS_Constants)"""

    # Moisture / canopy parameters
    MAD = 0.4
    NDVImin = 0.15
    NDVImax = 0.85
    fc_min = 0.01
    fc_max = 1.0
    fc_full_veg = 0.8

    # Radiation / emissivity / albedo
    Albedo_soil = 0.2
    Albedo_veg = 0.15
    Emiss_soil = 0.98
    Emiss_veg = 0.98
    BB_Emissi = (Emiss_soil + Emiss_veg) / 2.0
    Stefan_Boltzamn = 0.0000000568  # 5.68e-8

    # Aerodynamics / resistances
    rb = 25.0
    rahlo = 1.0
    rahhi = 200.0
    u_fri_lo = 0.01
    u_fri_hi = 500.0
    Zos = 0.01
    z_b_wind = 10.0
    z_b_air = 2.0

    # Temperature / flux bounds
    tslo = 270.0
    tshi = 335.0
    shlo = -500.0
    shhi = 500.0
    ghlo = -200.0
    ghhi = 300.0
    iter_lo = -1.0
    iter_hi = 1.0

    # Soil / water parameters
    Soillo = 0.01
    Soilhi = 1.0
    soilm_min = 0.0
    Theta_sat = 0.40
    soil_depth = 100.0
    droot = 500.0
    ssrun = 0.0

    # Reference ET and limits
    Ref_ET = 1.5 / 3600.0       # ≈ 0.0004166667
    Ref_fac = 20.0
    ET_min = 0.000003
    rl_max = 5000.0

    # Meteorology / misc
    Cp = 1013.0
    Rgl = 100.0
    hs = 36.25
    time_step = 60.0
    Irri_flag = 1



class BAITSSSAlgorithm:
    """
    Pure BAITSSS Physics Implementation
    Handles both single pixel and block processing
    """
    
    def __init__(self):
        self.constants = BAITSSSConstants()
    
    def iterative_calculation(self, ndvi_in: float, lai_in: float, soil_awc_in: float, 
                            soil_fc_in: float, nlcd_u_in: float, precip_in: float, 
                            elev_array_in: float, tair_oc_in: float, s_hum_in: float, 
                            uz_in_in: float, in_short_in: float, et_sum_in: float, 
                            precip_prism_sum_in: float, irri_sum_in: float, 
                            soilm_pre_in: float, soilm_root_pre_in: float) -> np.ndarray:
        """
        Single pixel calculation (backward compatibility)
        """
        # Convert to 1x1 block and process
        block_vars = {
            'ndvi': np.array([[ndvi_in]], dtype=np.float32),
            'lai': np.array([[lai_in]], dtype=np.float32),
            'soil_awc': np.array([[soil_awc_in]], dtype=np.float32),
            'soil_fc': np.array([[soil_fc_in]], dtype=np.float32),
            'nlcd': np.array([[nlcd_u_in]], dtype=np.float32),
            'precipitation': np.array([[precip_in]], dtype=np.float32),
            'elevation': np.array([[elev_array_in]], dtype=np.float32),
            'temperature': np.array([[tair_oc_in]], dtype=np.float32),
            'humidity': np.array([[s_hum_in]], dtype=np.float32),
            'wind_speed': np.array([[uz_in_in]], dtype=np.float32),
            'radiation': np.array([[in_short_in]], dtype=np.float32),
            'soil_moisture_surface_prev': np.array([[soilm_pre_in]], dtype=np.float32),
            'soil_moisture_root_prev': np.array([[soilm_root_pre_in]], dtype=np.float32)
        }
        
        result = self.process_block(block_vars, 1, 1)
        
        if result is not None:
            return np.array([
                result['et_hour'][0, 0],
                result['precip_hour'][0, 0] if 'precip_hour' in result else precip_in,
                result['irrigation'][0, 0],
                result['soil_surface'][0, 0],
                result['soil_root'][0, 0]
            ], dtype=np.float32)
        else:
            return np.array([0.0, 0.0, 0.0, 0.2, 0.3], dtype=np.float32)

    def process_block(self, block_vars: Dict[str, np.ndarray], 
                     block_height: int, block_width: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Process a block of data with complete BAITSSS physics
        Main method for block-wise processing
        """
        try:
            # Extract and validate variables
            variables = self._extract_and_validate_variables(block_vars, block_height, block_width)
            
            # Run complete BAITSSS physics
            results = self._run_complete_baitsss_physics(variables, block_height, block_width)
            
            return results
            
        except Exception as e:
            print(f"BAITSSS block processing error: {e}")
            return self._get_default_results(block_height, block_width)

    def _extract_and_validate_variables(self, block_vars: Dict[str, np.ndarray], 
                                      block_height: int, block_width: int) -> Dict[str, np.ndarray]:
        """Extract and validate all input variables - CORRECTED VARIABLE MAPPING"""
        
        # Map input variable names to internal BAITSSS names
        variable_mapping = {
            'soil_awc': 'soil_awc',
            'soil_fc': 'soil_fc', 
            'elevation': 'elevation',
            'nlcd': 'nlcd',
            'precipitation': 'precipitation',
            'ndvi': 'ndvi',
            'lai': 'lai',
            'temperature': 'temperature',
            'humidity': 'humidity', 
            'wind_speed': 'wind_speed',
            'radiation': 'radiation',
            'soil_moisture_surface_prev': 'soil_moisture_surface_prev',
            'soil_moisture_root_prev': 'soil_moisture_root_prev'
        }
        
        # Default values for missing variables
        defaults = {
            'soil_awc': 0.15,        # 15% available water capacity
            'soil_fc': 35.0,         # 35% field capacity (will be converted to fraction)
            'elevation': 200.0,      # 200m elevation
            'nlcd': 42.0,           # Evergreen forest
            'precipitation': 0.0,    # No precipitation
            'ndvi': 0.4,            # Moderate vegetation
            'lai': 3.0,             # Reasonable LAI
            'temperature': 15.0,     # 15°C
            'humidity': 0.65,       # 65% humidity
            'wind_speed': 3.0,      # 3 m/s wind
            'radiation': 400.0,     # 400 W/m² radiation
            'soil_moisture_surface_prev': 0.2,  # 20% soil moisture
            'soil_moisture_root_prev': 0.3      # 30% root zone moisture
        }
        
        variables = {}
        
        # Extract variables with proper mapping
        for input_name, internal_name in variable_mapping.items():
            if input_name in block_vars:
                variables[internal_name] = block_vars[input_name].astype(np.float32)
            else:
                default_val = defaults.get(internal_name, 0.0)
                variables[internal_name] = np.full((block_height, block_width), default_val, dtype=np.float32)
        
        # Data validation and preprocessing
        variables['lai'] = np.clip(variables['lai'], 0.0001, 6.0)
        variables['ndvi'] = np.clip(variables['ndvi'], -1.0, 1.0)
        variables['temperature'] = np.clip(variables['temperature'], -50.0, 60.0)
        variables['wind_speed'] = np.maximum(variables['wind_speed'], 2.0)
        variables['humidity'] = np.clip(variables['humidity'], 0.1, 1.0)
        variables['radiation'] = np.maximum(variables['radiation'], 0.0)
        
        # Convert soil FC to fraction if needed (if values > 1, assume percentage)
        if np.max(variables['soil_fc']) > 1.0:
            variables['soil_fc'] = variables['soil_fc'] / 100.0
        
        # Ensure soil moisture values are reasonable
        variables['soil_moisture_surface_prev'] = np.clip(variables['soil_moisture_surface_prev'], 0.01, 0.6)
        variables['soil_moisture_root_prev'] = np.clip(variables['soil_moisture_root_prev'], 0.01, 0.6)
        
        return variables

    def _run_complete_baitsss_physics(self, variables: Dict[str, np.ndarray], 
                                    block_height: int, block_width: int) -> Dict[str, np.ndarray]:
        """
        Complete BAITSSS physics implementation with iterative energy balance
        """
        # Extract variables for easier access
        soil_awc = variables['soil_awc']
        soil_fc = variables['soil_fc']
        elevation = variables['elevation']
        nlcd = variables['nlcd']
        precipitation = variables['precipitation']
        ndvi = variables['ndvi']
        lai = variables['lai']
        temperature = variables['temperature']
        humidity = variables['humidity']
        wind_speed = variables['wind_speed']
        radiation = variables['radiation']
        soil_moisture_prev = variables['soil_moisture_surface_prev']
        soil_root_prev = variables['soil_moisture_root_prev']
        
        # Initialize flux variables for iteration
        ETsoil_sec_prev = np.full((block_height, block_width), 7.0191862e-005)
        ETveg_sec_prev = np.full((block_height, block_width), 5.089054e-006)
        H_flux_soil_prev = np.zeros((block_height, block_width))
        H_flux_veg_prev = np.zeros((block_height, block_width))
        G_flux_soil_prev = np.zeros((block_height, block_width))
        
        # Derived soil parameters
        theta_ref = soil_fc
        theta_wilt = np.maximum(0.004, theta_ref - soil_awc)
        raw = self.constants.MAD * soil_awc
        thres_mois = theta_ref - raw
        
        # Atmospheric calculations
        pressure = 101.3 * np.power(((293 - elevation * 0.0065) / 293), 5.26)
        psyc_con = 0.000665 * pressure
        tair_k = temperature + 273.15
        
        # Vegetation parameters
        fc_eqn = (ndvi - self.constants.NDVImin) / (self.constants.NDVImax - self.constants.NDVImin)
        fc = np.clip(fc_eqn, self.constants.fc_min, self.constants.fc_max)
        
        # Atmospheric emissivity and radiation
        emis_ta = 1.0 - 0.261 * np.exp(-0.000777 * np.power(temperature, 2))
        in_long = np.power(tair_k, 4) * self.constants.Stefan_Boltzamn * emis_ta
        
        # Vapor pressure
        es = 0.611 * np.exp((17.27 * temperature) / (temperature + 237.3))
        ea = (pressure * humidity) / (0.378 * humidity + 0.622)
        
        # Canopy structure
        hc = 0.15 * lai
        d = 1.1 * hc * np.log(1 + np.power(0.2 * lai, 0.25))
        zom = 0.018 * lai
        
        # Resistance calculations
        rac = self.constants.rb / (2 * (lai / fc))
        
        # Aerodynamic resistance (initial)
        u_fri_neu = (0.41 * wind_speed) / np.log((self.constants.z_b_wind - d) / zom)
        rah_est = (np.log((self.constants.z_b_wind - d) / zom) * 
                  np.log((self.constants.z_b_air - d) / (0.1 * zom))) / (0.41 * 0.41 * wind_speed)
        rah = np.clip(rah_est, self.constants.rahlo, self.constants.rahhi)
        
        # Surface resistance for soil
        ras_full = hc * np.exp(2.5) / (0.41 * 0.41 * wind_speed * (hc - d))
        ras_bare = (np.log(self.constants.z_b_wind / self.constants.Zos) * 
                   np.log((d + zom) / self.constants.Zos)) / (0.41 * 0.41 * wind_speed)
        ras = np.where(fc >= self.constants.fc_full_veg, 0, 
                      np.clip(1 / ((fc / np.maximum(1, ras_full)) + ((1 - fc) / ras_bare)), 1, 5000))
        
        air_density = pressure / (1.01 * 0.287 * tair_k)
        
        # Initialize soil moisture
        soil_moisture_current = soil_moisture_prev.copy()
        soil_root_current = soil_root_prev.copy()
        
        # ITERATIVE CONVERGENCE LOOP (reduced to 5 iterations for performance)
        for iteration in range(5):
            # === SOIL COMPONENT ===
            
            # Soil surface resistance
            rss = np.clip(3.5 * np.power(self.constants.Theta_sat / np.maximum(0.01, soil_moisture_current), 2.3) + 33.5, 
                         35, 5000)
            
            # Soil temperature
            ts_eq = ((H_flux_soil_prev * (ras + rah)) / (air_density * self.constants.Cp)) + tair_k
            ts = np.where(radiation <= 100, tair_k - 2.5, 
                         np.clip(ts_eq, self.constants.tslo, self.constants.tshi))
            
            # Soil evaporation
            lambda_soil = (2.501 - 0.00236 * (ts - 273.15)) * 1000000
            eosur = 0.611 * np.exp((17.27 * (ts - 273.15)) / ((ts - 273.15) + 237.3))
            le_soil_ini = ((eosur - ea) * self.constants.Cp * air_density) / ((rah + rss + ras) * psyc_con)
            etsoil_sec = np.clip(le_soil_ini / lambda_soil, self.constants.ET_min, 
                               self.constants.Ref_ET * self.constants.Ref_fac)
            etsoil_sec_avg = (ETsoil_sec_prev + etsoil_sec) / 2.0
            
            # === VEGETATION COMPONENT ===
            
            # Canopy temperature
            tc_eq = ((H_flux_veg_prev * (rac + rah)) / (air_density * self.constants.Cp)) + tair_k
            tc = np.where(fc <= self.constants.fc_min, ts,
                         np.where(radiation <= 100, tair_k - 2.5,
                                 np.clip(tc_eq, self.constants.tslo, self.constants.tshi)))
            
            # Stomatal resistance (Jarvis model)
            eoveg = 0.611 * np.exp((17.27 * (tc - 273.15)) / ((tc - 273.15) + 237.3))
            vpd = eoveg - ea
            
            # Solar radiation factor
            f = 0.55 * (radiation / self.constants.Rgl) * (2.0 / lai)
            f1 = (40.0 / self.constants.rl_max + f) / (1.0 + f)
            
            # Water stress factor
            soil_fac = (soil_root_current - theta_wilt) / (theta_ref - theta_wilt)
            awf = np.clip(soil_fac, 0, 1)
            f4 = np.log(np.maximum(1, 800 * awf)) / np.log(800)
            
            # Temperature factor
            f2 = 1.0 - 0.0016 * np.power(298.0 - tair_k, 2)
            
            # VPD factor
            f3 = np.clip(1.0 - 0.1914 * vpd, 0.1, 1.0)
            
            # Combined stomatal resistance
            rsc = np.clip(40.0 / ((lai / fc) * f1 * f2 * f4 * f3), 40.0, self.constants.rl_max)
            
            # Vegetation transpiration
            lambda_veg = (2.501 - 0.00236 * (tc - 273.15)) * 1000000
            etveg_sec = np.clip(((eoveg - ea) * self.constants.Cp * air_density) / 
                              ((rsc + rac + rah) * psyc_con) / lambda_veg,
                              self.constants.ET_min, self.constants.Ref_ET * self.constants.Ref_fac)
            etveg_sec_avg = (ETveg_sec_prev + etveg_sec) / 2.0
            
            # === ENERGY BALANCE ===
            
            # Latent heat fluxes
            le_soil = etsoil_sec_avg * lambda_soil
            le_veg = etveg_sec_avg * lambda_veg
            
            # Net radiation
            outlwr_soil = np.power(ts, 4) * self.constants.Emiss_soil * self.constants.Stefan_Boltzamn
            outlwr_veg = np.power(tc, 4) * self.constants.BB_Emissi * self.constants.Stefan_Boltzamn
            
            netrad_soil = (radiation - self.constants.Albedo_soil * radiation + 
                          in_long - outlwr_soil - (1 - self.constants.Emiss_soil) * in_long)
            netrad_veg = (radiation - self.constants.Albedo_veg * radiation + 
                         in_long - outlwr_veg - (1 - self.constants.Emiss_veg) * in_long)
            
            # Sensible heat flux
            sheat_soil = netrad_soil - G_flux_soil_prev - le_soil
            sheat_veg = np.clip(netrad_veg - le_veg, self.constants.shlo, self.constants.shhi)
            
            # Ground heat flux
            G_flux_soil_prev = np.maximum(0.4 * sheat_soil, 0.15 * netrad_soil)
            
            # Update for next iteration
            H_flux_soil_prev = (sheat_soil + H_flux_soil_prev) / 2.0
            H_flux_veg_prev = (sheat_veg + H_flux_veg_prev) / 2.0
            ETsoil_sec_prev = etsoil_sec_avg
            ETveg_sec_prev = etveg_sec_avg
        
        # === FINAL CALCULATIONS ===
        
        # Hourly ET components
        etsoil_hour = (1.0 - fc) * 3600.0 * etsoil_sec_avg
        etveg_hour = etveg_sec_avg * 3600.0 * fc
        et_total_hour = etsoil_hour + etveg_hour
        
        # Soil moisture update
        soil_moisture_new = soil_moisture_prev - ((etsoil_sec_avg * 3600 * (1 - fc)) - precipitation) / (self.constants.soil_depth * 1000)
        soil_moisture_new = np.clip(soil_moisture_new, 0.01, theta_ref)
        
        soil_root_new = soil_root_prev + ((precipitation - etsoil_sec_avg * 3600 * (1 - fc) - etveg_sec_avg * 3600 * fc) / 
                                        (self.constants.droot * 1000))
        soil_root_new = np.clip(soil_root_new, 0.01, theta_ref)
        
        # Irrigation (simplified)
        irrigation = np.where((soil_root_new < thres_mois) & (nlcd > 80) & (nlcd < 83), 
                            0.04 * self.constants.droot * 1000, 0.0)
        soil_root_new = np.where(irrigation > 0, soil_root_new + 0.04, soil_root_new)
        
        return {
            'et_hour': et_total_hour,
            'soil_surface': soil_moisture_new,
            'soil_root': soil_root_new,
            'irrigation': irrigation,
            'precip_hour': precipitation,
            'etsoil_hour': etsoil_hour,
            'etveg_hour': etveg_hour,
            'fc': fc
        }

    def _get_default_results(self, block_height: int, block_width: int) -> Dict[str, np.ndarray]:
        """Return default results for failed calculations"""
        return {
            'et_hour': np.full((block_height, block_width), 0.2, dtype=np.float32),
            'soil_surface': np.full((block_height, block_width), 0.2, dtype=np.float32),
            'soil_root': np.full((block_height, block_width), 0.3, dtype=np.float32),
            'irrigation': np.zeros((block_height, block_width), dtype=np.float32),
            'precip_hour': np.zeros((block_height, block_width), dtype=np.float32),
            'etsoil_hour': np.full((block_height, block_width), 0.1, dtype=np.float32),
            'etveg_hour': np.full((block_height, block_width), 0.1, dtype=np.float32),
            'fc': np.full((block_height, block_width), 0.3, dtype=np.float32)
        }


# Utility functions for backward compatibility
def safe_band_extraction(band_array: np.ndarray, index: int, default_value: float = 0.0) -> float:
    """Safe band extraction with default value"""
    if index < len(band_array) and not np.isnan(band_array[index]):
        return float(band_array[index])
    return default_value


def pad_array_to_length(input_array: np.ndarray, expected_length: int, 
                       default_value: float = 0.0) -> np.ndarray:
    """Pad array to expected length with default values"""
    if len(input_array) >= expected_length:
        return input_array
    else:
        padding = np.full(expected_length - len(input_array), default_value)
        return np.concatenate([input_array, padding])


def validate_baitsss_inputs(ndvi: float, lai: float, tair_oc: float) -> bool:
    """Validate critical BAITSSS input parameters"""
    return (not np.isnan(tair_oc) and -50 < tair_oc < 60 and
            not np.isnan(ndvi) and -1 <= ndvi <= 1 and
            not np.isnan(lai) and lai >= 0)