#!/usr/bin/env python3
"""
BAITSSS ET Algorithm - Complete Python Implementation
Biosphere-Atmosphere Interactions Two-Source Surface Model
Exact translation of the Scala BAITSSS implementation
"""

import numpy as np
import math
import random
from typing import Dict, Tuple


class BAITSSSConstants:
    """
    Physical constants for BAITSSS ET model
    Values exactly matching the Scala BAITSSS_Constants implementation
    """
    # Soil and vegetation parameters
    MAD = 0.5  # Management Allowed Depletion
    NDVImin = 0.2
    NDVImax = 0.8
    fc_min = 0.1
    fc_max = 0.8
    fc_full_veg = 0.8
    
    # Physical constants
    Stefan_Boltzamn = 5.67e-8  # Stefan-Boltzmann constant (W/m²/K⁴)
    Cp = 1013.0  # Specific heat of air (J/kg/K)
    
    # Surface properties
    Albedo_soil = 0.15
    Albedo_veg = 0.25
    Emiss_soil = 0.95
    Emiss_veg = 0.98
    BB_Emissi = 0.98
    
    # Resistance parameters
    rb = 50.0  # Boundary layer resistance
    rahlo = 5.0   # Minimum aerodynamic resistance
    rahhi = 500.0  # Maximum aerodynamic resistance
    u_fri_lo = 0.1  # Minimum friction velocity
    u_fri_hi = 1.0   # Maximum friction velocity
    
    # Temperature limits (Kelvin)
    tshi = 343.15  # Maximum surface temperature (70°C)
    tslo = 263.15  # Minimum surface temperature (-10°C)
    
    # Sensible heat flux limits (W/m²)
    shlo = -200.0  # Minimum sensible heat flux
    shhi = 800.0   # Maximum sensible heat flux
    
    # Reference heights (meters)
    z_b_wind = 10.0  # Wind measurement height
    z_b_air = 2.0    # Air temperature measurement height
    Zos = 0.01       # Roughness length for bare soil
    
    # Stomatal resistance parameters
    rl_max = 5000.0  # Maximum stomatal resistance (s/m)
    Rgl = 100.0      # Global radiation threshold (W/m²)
    
    # Soil parameters
    Theta_sat = 0.5   # Saturated soil moisture (m³/m³)
    soil_depth = 0.1  # Surface soil depth (m)
    droot = 1.0       # Root zone depth (m)
    time_step = 1.0   # Time step (hours)
    ssrun = 0.0       # Surface runoff (mm)
    
    # ET parameters
    Ref_ET = 0.0003   # Reference ET rate (m/s)
    Ref_fac = 2.0     # Reference ET factor
    ET_min = 1e-6     # Minimum ET rate (m/s)
    
    # Irrigation parameters
    Irri_flag = 1     # Irrigation flag (1 = enabled)


class BAITSSSAlgorithm:
    """
    Complete BAITSSS ET Algorithm Implementation
    Two-source energy balance model for evapotranspiration
    Exact Python translation of the Scala implementation
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
        Complete BAITSSS iterative calculation
        Exact Python translation of the Scala BandCalculationCheck_test.IterativeCalculation
        
        Returns:
            Array of 5 values: [ET_sum, precip_prism_sum, irri_sum, soilm_cur_final, soilm_root_final]
        """
        
        # Initialize with small random perturbations (matching Scala)
        rl_min = 40.0  # Minimum stomatal resistance
        
        # Add small random noise to inputs
        ndvi = ndvi_in + (random.random() * 0.01)
        lai = max(0.0001, min(6.0, lai_in)) + (random.random() * 0.01)
        
        # Initialize all variables
        soil_awc = soil_awc_in
        soil_fc = soil_fc_in
        nlcd_u = nlcd_u_in
        precip = precip_in
        elev_array = elev_array_in
        tair_oc = tair_oc_in
        s_hum = s_hum_in
        uz_in = uz_in_in
        in_short = in_short_in
        
        # State variables from previous time step
        soilm_pre = soilm_pre_in
        soilm_root_pre = soilm_root_pre_in
        et_sum = et_sum_in
        precip_prism_sum = precip_prism_sum_in
        irri_sum = irri_sum_in
        
        # Initialize flux variables
        h_flux_rep_soil_pre = 0.0
        g_flux_rep_soil_pre = 0.0
        g_flux_rep_veg_pre = 0.0
        h_flux_rep_veg_pre = 0.0
        etsoil_sec_pre = 7.0191862e-005
        etveg_sec_pre = 5.089054e-006
        
        irri_app = 0.0
        
        # Basic physical calculations
        zom = 0.018 * lai
        theta_ref = soil_fc / 100.0
        theta_wilt = max(0.004, theta_ref - soil_awc)
        raw = self.constants.MAD * soil_awc
        thres_mois = theta_ref - raw
        
        # Atmospheric pressure calculation
        pressure_val = 101.3 * math.pow((293.0 - elev_array * 0.0065) / 293.0, 5.26)
        psyc_con = 0.000665 * pressure_val
        tair = tair_oc + 273.15
        
        # Fractional cover calculation
        fc_eqn = (ndvi - self.constants.NDVImin) / (self.constants.NDVImax - self.constants.NDVImin)
        fc = max(self.constants.fc_min, min(self.constants.fc_max, fc_eqn))
        
        # Atmospheric emissivity and longwave radiation
        emis_ta = 1.0 - 0.261 * math.exp(-0.000777 * (tair_oc * tair_oc))
        in_long = math.pow(tair, 4) * self.constants.Stefan_Boltzamn * emis_ta
        
        # Vapor pressure calculations
        es = 0.611 * math.exp((17.27 * tair_oc) / (tair_oc + 237.3))
        ea = (pressure_val * s_hum) / (0.378 * s_hum + 0.622)
        
        # Canopy structure calculations
        hc = 0.15 * lai
        d = 1.1 * hc * math.log(1.0 + math.pow(0.2 * lai, 0.25))
        
        # Wind profile parameters
        if hc < 1:
            n = 2.5
        elif hc > 10:
            n = 4.25
        else:
            n = 2.31 + 0.194 * hc
        
        # Roughness length calculations
        if fc <= 0.6:
            z1 = hc - d
        elif fc >= self.constants.fc_full_veg:
            z1 = 0.1 * zom
        else:
            z1 = (hc - d) - (((hc - d) - (0.1 * zom)) * (fc - 0.6)) / (1.0 - 0.6)
        
        # Aerodynamic resistance calculations
        rac = self.constants.rb / (2.0 * (lai / fc))
        uz = max(2.0, uz_in)  # Minimum wind speed
        
        # Aerodynamic conductance
        kh = (0.41 * 0.41 * uz * (hc - d)) / math.log(abs(self.constants.z_b_wind - d) / zom)
        
        # Surface resistance calculations
        ras_full_act_eqn = (hc * math.exp(n) * 
                           (math.exp(-n * zom / hc) - math.exp(-n * (d + zom) / hc)) / 
                           (n * kh))
        ras_full_ini = max(1.0, ras_full_act_eqn)
        
        ras_bare_ini = (math.log(self.constants.z_b_wind / self.constants.Zos) * 
                       math.log((d + zom) / self.constants.Zos)) / (0.41 * 0.41 * uz)
        
        ras_ini = 1.0 / ((fc / ras_full_ini) + ((1.0 - fc) / ras_bare_ini))
        
        if fc >= self.constants.fc_full_veg:
            ras = 0.0
        else:
            ras = max(1.0, min(5000.0, ras_ini))
        
        # Air density
        air_den = pressure_val / (1.01 * 0.287 * tair)
        
        # Initial friction velocity and aerodynamic resistance
        u_fri_neu = (0.41 * uz) / math.log(abs(self.constants.z_b_wind - d) / zom)
        rah_est = (math.log(abs(self.constants.z_b_wind - d) / zom) * 
                  math.log(abs(self.constants.z_b_air - d) / z1)) / (0.41 * 0.41 * uz)
        rah = max(self.constants.rahlo, min(self.constants.rahhi, rah_est))
        
        # Initial soil moisture calculations
        soilm_cur = (soilm_pre - 
                    ((etsoil_sec_pre * 60.0 * self.constants.time_step) * (1.0 - fc) - 
                     (precip - self.constants.ssrun)) / self.constants.soil_depth)
        soilm_cur_final = max(0.01, min(theta_ref, soilm_cur))
        
        # **ITERATIVE CONVERGENCE LOOP - 10 ITERATIONS**
        for iteration in range(1, 11):
            # Update previous iteration values
            etsoil_sec_pre_iter = etsoil_sec_pre
            etveg_sec_pre_iter = etveg_sec_pre
            
            # === SOIL COMPONENT CALCULATIONS ===
            
            # Soil moisture update
            soilm_cur = (soilm_pre - 
                        ((etsoil_sec_pre * 60.0 * self.constants.time_step * (1.0 - fc)) - 
                         (precip - self.constants.ssrun)) / self.constants.soil_depth)
            soilm_cur_final = max(0.01, min(theta_ref, soilm_cur))
            
            # Soil surface resistance
            rss_eqn = 3.5 * math.pow(self.constants.Theta_sat / soilm_cur_final, 2.3) + 33.5
            rss = max(35.0, min(5000.0, rss_eqn))
            
            # Soil surface temperature
            tsurf_eq_soil = ((h_flux_rep_soil_pre * (ras + rah)) / 
                           (air_den * self.constants.Cp)) + tair
            
            if in_short <= 100:
                ts = tair - 2.5
            else:
                ts = max(self.constants.tslo, min(self.constants.tshi, tsurf_eq_soil))
            
            # Soil evaporation calculations
            lambda_soil = (2.501 - 0.00236 * (ts - 273.15)) * 1000000.0
            eosur = 0.611 * math.exp((17.27 * (ts - 273.15)) / ((ts - 273.15) + 237.3))
            le_soil_ini = ((eosur - ea) * self.constants.Cp * air_den) / ((ras + rah + rss) * psyc_con)
            etsoil_sec_eqn = le_soil_ini / lambda_soil
            
            etsoil_sec = max(self.constants.ET_min, 
                           min(self.constants.Ref_ET * self.constants.Ref_fac, etsoil_sec_eqn))
            etsoil_sec_ave = (etsoil_sec_pre + etsoil_sec) / 2.0
            
            # Soil energy balance
            le_soil = etsoil_sec_ave * lambda_soil
            outlwr_soil = (math.pow(ts, 4) * self.constants.Emiss_soil * 
                          self.constants.Stefan_Boltzamn)
            netrad_soil = (in_short - (self.constants.Albedo_soil * in_short) + 
                          in_long - outlwr_soil - 
                          (1.0 - self.constants.Emiss_soil) * in_long)
            sheat_soil = netrad_soil - g_flux_rep_soil_pre - le_soil
            
            # Soil heat flux
            gheat_h = 0.4 * sheat_soil
            gheat_netrad = 0.15 * netrad_soil
            gheat_soil = max(gheat_h, gheat_netrad)
            g_flux_rep_soil_pre = gheat_soil
            
            # === ROOT ZONE SOIL MOISTURE AND IRRIGATION ===
            
            soilm_root = (soilm_root_pre + 
                         ((precip + irri_app - self.constants.ssrun) - 
                          (etveg_sec_pre * 60.0 * self.constants.time_step * fc) - 
                          (etsoil_sec * 60.0 * self.constants.time_step * (1.0 - fc))) / 
                         self.constants.droot)
            soilm_root_limit = max(0.01, min(theta_ref, soilm_root))
            
            # Irrigation logic
            irri_amount = 0.04 * self.constants.droot
            if (self.constants.Irri_flag == 1 and soilm_root_limit < thres_mois and 
                80 < nlcd_u < 83):
                irrigation = irri_amount
                soilm_root_final = soilm_root_limit + 0.04
            else:
                irrigation = 0.0
                soilm_root_final = soilm_root_limit
            
            # Surface irrigation update (sprinkler system)
            if irrigation == irri_amount:
                soilm_cur_final = soilm_root_final
            
            # === VEGETATION COMPONENT CALCULATIONS ===
            
            # Canopy temperature
            tveg_eq = ((h_flux_rep_veg_pre * (rac + rah)) / 
                      (air_den * self.constants.Cp)) + tair
            
            if fc <= self.constants.fc_min:
                tc = ts
            elif in_short <= 100:
                tc = tair - 2.5
            else:
                tc = max(self.constants.tslo, min(self.constants.tshi, tveg_eq))
            
            # Vegetation vapor pressure and VPD
            eoveg = 0.611 * math.exp((17.27 * (tc - 273.15)) / ((tc - 273.15) + 237.3))
            lambda_veg = (2.501 - 0.00236 * (tc - 273.15)) * 1000000.0
            vpd = eoveg - ea
            
            # === STOMATAL RESISTANCE CALCULATION (JARVIS MODEL) ===
            
            # Solar radiation factor
            f = 0.55 * (in_short / self.constants.Rgl) * (2.0 / lai)
            f1 = ((rl_min / self.constants.rl_max) + f) / (1.0 + f)
            
            # Soil water stress factor
            soil_fac = (soilm_root_final - theta_wilt) / (theta_ref - theta_wilt)
            awf = max(0.0, min(1.0, soil_fac))
            
            # Water stress function
            wo = 1.0
            wf = 800.0
            w = (wo * wf) / (wo + (wf - wo) * math.exp(-12.0 * awf))
            f4 = math.log(abs(w)) / math.log(wf)
            
            # Temperature factor
            b5 = 0.0016
            f2 = 1.0 - b5 * (298.0 - tair) * (298.0 - tair)
            
            # Vapor pressure deficit factor
            c3 = 0.1914
            f3_con = 1.0 - c3 * vpd
            f3 = max(0.1, min(1.0, f3_con))
            
            # Combined stomatal resistance
            rsc = rl_min / ((lai / fc) * f1 * f2 * f4 * f3)
            rsc_final = max(rl_min, min(self.constants.rl_max, rsc))
            
            # === VEGETATION TRANSPIRATION ===
            
            etveg_sec_eqn = (((eoveg - ea) * self.constants.Cp * air_den) / 
                           ((rsc_final + rac + rah) * psyc_con)) / lambda_veg
            etveg_sec = max(self.constants.ET_min, 
                          min(self.constants.Ref_ET * self.constants.Ref_fac, etveg_sec_eqn))
            etveg_sec_ave = (etveg_sec_pre + etveg_sec) / 2.0
            
            # Vegetation energy balance
            le_veg = lambda_veg * etveg_sec_ave
            outlwr_veg = (math.pow(tc, 4) * self.constants.BB_Emissi * 
                         self.constants.Stefan_Boltzamn)
            netrad_veg = (in_short - (self.constants.Albedo_veg * in_short) + 
                         in_long - outlwr_veg - 
                         (1.0 - self.constants.Emiss_veg) * in_long)
            sheat_1_veg = netrad_veg - le_veg
            sheat_veg = max(self.constants.shlo, min(self.constants.shhi, sheat_1_veg))
            
            # === ATMOSPHERIC STABILITY CORRECTIONS ===
            
            # Combined sensible heat flux
            sheat = sheat_veg * fc + (1.0 - fc) * sheat_soil
            
            # Monin-Obukhov length
            l_mo = (-1.0 * air_den * self.constants.Cp * tair * math.pow(u_fri_neu, 3)) / (sheat * 4.02)
            l_mo = max(-500.0, min(500.0, l_mo))
            
            # Stability correction functions
            x_z_b_wind = math.pow(1.0 - (16.0 * (self.constants.z_b_wind - d)) / l_mo, 0.25)
            
            if l_mo <= 0:
                eqn51 = (2.0 * math.log((1.0 + x_z_b_wind) / 2.0) + 
                        math.log((1.0 + math.pow(x_z_b_wind, 2)) / 2.0) - 
                        2.0 * math.atan(x_z_b_wind) + 1.5708)
                psi_m_z_b_wind = eqn51
            else:
                psi_m_z_b_wind = -5.0 * self.constants.z_b_wind / l_mo
            
            # Air temperature stability correction
            x_z_b_air = math.pow(1.0 - (16.0 * (self.constants.z_b_air - d)) / l_mo, 0.25)
            
            if l_mo <= 0:
                psi_h_z_b_air = 2.0 * math.log((1.0 + math.pow(x_z_b_air, 2)) / 2.0)
            else:
                psi_h_z_b_air = -5.0 * self.constants.z_b_air / l_mo
            
            # Updated friction velocity
            u_fri = ((0.41 * uz) / 
                    (math.log(abs(self.constants.z_b_wind - d) / zom) - psi_m_z_b_wind))
            u_fri = max(self.constants.u_fri_lo, min(self.constants.u_fri_hi, u_fri))
            
            # Additional stability corrections for partial canopy
            x_dzom = math.pow(1.0 - (16.0 * (d + zom)) / l_mo, 0.25)
            if l_mo <= 0:
                psi_h_dzom = 2.0 * math.log((1.0 + math.pow(x_dzom, 2)) / 2.0)
            else:
                psi_h_dzom = -5.0 * (d + zom) / l_mo
            
            x_hd = math.pow(1.0 - (16.0 * (hc - d)) / l_mo, 0.25)
            if fc >= self.constants.fc_full_veg:
                psi_h_hd = 0.0
            elif l_mo <= 0:
                psi_h_hd = 2.0 * math.log((1.0 + math.pow(x_hd, 2)) / 2.0)
            else:
                psi_h_hd = -5.0 * hc / l_mo
            
            # Updated aerodynamic resistance
            rah_con = ((math.log(abs(self.constants.z_b_air - d) / z1) - 
                       psi_h_z_b_air + psi_h_hd) / (0.41 * u_fri))
            rah = max(self.constants.rahlo, min(self.constants.rahhi, rah_con))
            
            # Update flux variables for next iteration
            h_flux_new_soil = (sheat_soil + h_flux_rep_soil_pre) / 2.0
            h_flux_rep_soil_pre = h_flux_new_soil
            h_flux_new_veg = (sheat_veg + h_flux_rep_veg_pre) / 2.0
            h_flux_rep_veg_pre = h_flux_new_veg
            u_fri_new = (u_fri + u_fri_neu) / 2.0
            u_fri_neu = u_fri_new
            
            # Update ET values for next iteration
            etsoil_sec_pre = etsoil_sec_ave
            etveg_sec_pre = etveg_sec_ave
        
        # === FINAL CALCULATIONS AFTER CONVERGENCE ===
        
        # Calculate final hourly ET components
        etsoil_hour = (1.0 - fc) * 3600.0 * etsoil_sec_ave
        etveg_hour = etveg_sec_ave * 3600.0 * fc
        ethour_com = etveg_sec_ave * 3600.0 * fc + (1.0 - fc) * 3600.0 * etsoil_sec_ave
        
        # Update cumulative sums
        et_sum += ethour_com
        
        random_noise = random.gauss(0, 0.05)  # ±5% variation
        et_sum_randomized = et_sum + random_noise
        
        # Update other cumulative variables
        precip_hour = 0.0  # Set to 0 as in Scala
        precip_sum = precip_hour
        precip_prism_sum += precip
        irri_sum += irrigation
        
        # Return final state array
        return np.array([
            et_sum,                    # Cumulative ET
            precip_prism_sum,         # Cumulative precipitation  
            irri_sum,                 # Cumulative irrigation
            soilm_cur_final,          # Final surface soil moisture
            soilm_root_final          # Final root zone soil moisture
        ], dtype=np.float32)


# Utility functions matching Scala implementation
def safe_band_extraction(band_array: np.ndarray, index: int, default_value: float = 0.0) -> float:
    """
    Safe band extraction with default value
    Matches Scala safeBand function
    """
    if index < len(band_array) and not np.isnan(band_array[index]):
        return float(band_array[index])
    return default_value


def pad_array_to_length(input_array: np.ndarray, expected_length: int, 
                       default_value: float = 0.0) -> np.ndarray:
    """
    Pad array to expected length with default values
    Matches Scala padArray function
    """
    if len(input_array) >= expected_length:
        return input_array
    else:
        padding = np.full(expected_length - len(input_array), default_value)
        return np.concatenate([input_array, padding])


def validate_baitsss_inputs(ndvi: float, lai: float, tair_oc: float) -> bool:
    """
    Validate critical BAITSSS input parameters
    """
    return (not np.isnan(tair_oc) and -50 < tair_oc < 60 and
            not np.isnan(ndvi) and -1 <= ndvi <= 1 and
            not np.isnan(lai) and lai >= 0)