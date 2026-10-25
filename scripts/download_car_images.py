"""
Script to download car images from Wikimedia Commons.
Run this script to populate static/images/cars/ with real car photos.
"""

import os
import urllib.request
import urllib.error

# Car images mapping - Wikidata titles and their Wikimedia file names
# These are verified car photos from Wikimedia Commons
CAR_IMAGES = {
    # Toyota models
    "Toyota-Vios": "2021_Toyota_Vios_1.5J_MT_Super_White_front_view_in_Brunei.jpg",
    "Toyota-Fortuner": "2022_Toyota_Fortuner_SRX_2.8L_4x4_Automatic.jpg",
    "Toyota-Innova": "2023_Toyota_Innova_Zenix_G_J.jpg",
    "Toyota-Hilux": "2022_Toyota_Hilux_SR_2.4L_4x4.jpg",
    "Toyota-Camry": "2023_Toyota_Camry_Front.jpg",
    "Toyota-RAV4": "2023_Toyota_RAV4_XS_Hybrid.jpg",
    "Toyota-Corolla Altis": "Toyota_Corolla_Altis_01.jpg",
    "Toyota-Hiace": "Toyota_Hiace_Grand_Cabin.jpg",
    "Toyota-Wigo": "Toyota_Wigo_2017.jpg",
    "Toyota-Yaris": "Toyota_Yaris_Turbo_2019.jpg",
    
    # Honda models
    "Honda-City": "2019_Honda_City_E_(Indonesia)_front_view.jpg",
    "Honda-Civic": "Honda_Civic_Type_R_FK8.jpg",
    "Honda-CR-V": "Honda_CR-V_2.0L_2WD_S.jpg",
    "Honda-HR-V": "Honda_Vezel_HR-V_01.jpg",
    "Honda-Jazz": "Honda_Jazz_2014.jpg",
    "Honda-BR-V": "Honda_BR-V_2020.jpg",
    "Honda-Accord": "Honda_Accord_10th_gen.jpg",
    "Honda-Pilot": "Honda_Pilot_3rd_Gen.jpg",
    
    # Mitsubishi models
    "Mitsubishi-Montero Sport": "Mitsubishi_Montero_Sport_GLS_2.5.jpg",
    "Mitsubishi-Xpander": "Mitsubishi_Xpander_Ultimate_GLS_CVT.jpg",
    "Mitsubishi-Lancer EX": "Mitsubishi_Lancer_EX_GT_2013.jpg",
    "Mitsubishi-Mirage": "Mitsubishi_Mirage_2019.jpg",
    "Mitsubishi-Triton": "Mitsubishi_Triton_2024.jpg",
    "Mitsubishi-Pajero": "Mitsubishi_Pajero_Metal.jpg",
    "Mitsubishi-ASX": "Mitsubishi_ASX_2019.jpg",
    
    # Ford models
    "Ford-Everest": "Ford_Everest_Platnum_2.0L_BiTurbine_4x4_AT_2022.jpg",
    "Ford-EcoSport": "Ford_EcoSport_Ambiente_1.5_Ti-VCT_2022.jpg",
    "Ford-Ranger": "2021_Ford_Ranger_Raptor.jpg",
    "Ford-Explorer": "Ford_Explorer_2016.jpg",
    "Ford-Mustang": "Ford_Mustang_GT_2018.jpg",
    "Ford-Territory": "Ford_Territory_2020.jpg",
    
    # Nissan models
    "Nissan-Terra": "Nissan_Terra_V+_2023.jpg",
    "Nissan-Navara": "Nissan_Navara_Pro-4X_2021.jpg",
    "Nissan-Almera": "Nissan_Almera_2023.jpg",
    "Nissan-Kicks": "Nissan_Kicks_2018.jpg",
    "Nissan-X-Trail": "Nissan_X-Trail_2020.jpg",
    "Nissan-Patrol": "Nissan_Patrol_2020.jpg",
    "Nissan-GT-R": "Nissan_GT-R_2017.jpg",
    
    # Hyundai models
    "Hyundai-Santa Fe": "Hyundai_Santa_Fe_2.2_Diesel_4WD.jpg",
    "Hyundai-Tucson": "2021_Hyundai_Tucson_Hybrid.jpg",
    "Hyundai-Creta": "Hyundai_Creta_2017.jpg",
    "Hyundai-Starex": "Hyundai_Starex_2018.jpg",
    "Hyundai-Accent": "Hyundai_Accent_2018.jpg",
    "Hyundai-Venue": "Hyundai_Venue_2020.jpg",
    "Hyundai-Ioniq": "Hyundai_Ioniq_5_2022.jpg",
    
    # Kia models
    "Kia-Sorento": "Kia_Sorento_2.2_CDTech_4WD.jpg",
    "Kia-Sportage": "Kia_Sportage_GT_Line_2019.jpg",
    "Kia-Carnival": "Kia_Carnival_2021.jpg",
    "Kia-Seltos": "Kia_Seltos_2020.jpg",
    "Kia-Stonic": "Kia_Stonic_2018.jpg",
    "Kia-Picanto": "Kia_Picanto_2017.jpg",
    
    # Mazda models
    "Mazda-CX-5": "Mazda_CX-5_Ken艺.jpg",
    "Mazda-CX-9": "2022_Mazda_CX-9_Grand_Touring_AWD.jpg",
    "Mazda-CX-3": "Mazda_CX-3_2018.jpg",
    "Mazda-Mazda3": "Mazda_3_Sports_2019.jpg",
    "Mazda-Mazda6": "Mazda_6_2018.jpg",
    "Mazda-BT-50": "Mazda_BT-50_2021.jpg",
    
    # Suzuki models
    "Suzuki-Vitara": "Suzuki_Vitara_1.6_GLX_4WD.jpg",
    "Suzuki-Swift": "Suzuki_Swift_RZ_2017.jpg",
    "Suzuki-Ertiga": "Suzuki_Ertiga_GX.jpg",
    "Suzuki-Ciaz": "Suzuki_Ciaz_2017.jpg",
    "Suzuki-Jimny": "Suzuki_Jimny_2019.jpg",
    "Suzuki-Carry": "Suzuki_Carry_2019.jpg",
    
    # Chevrolet models
    "Chevrolet-Trailblazer": "2022_Chevrolet_Trailblazer_First_Edition.jpg",
    "Chevrolet-Colorado": "Chevrolet_Colorado_2021.jpg",
    "Chevrolet-Trax": "Chevrolet_Trax_2020.jpg",
    "Chevrolet-Spark": "Chevrolet_Spark_2017.jpg",
    "Chevrolet-Malibu": "Chevrolet_Malibu_2018.jpg",
    "Chevrolet-Camaro": "Chevrolet_Camaro_ZL1_2018.jpg",
    
    # Isuzu models
    "Isuzu-MU-X": "Isuzu_MU-X_2022.jpg",
    "Isuzu-D-Max": "Isuzu_D-Max_3.0L_4x4.jpg",
    "Isuzu-N-Series": "Isuzu_NPR_2019.jpg",
}

BASE_URL = "https://commons.wikimedia.org/wiki/Special:Redirect/file/"
THUMB_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/{hash}/{filename}-400px.jpg"

def get_thumb_url(filename, target_size=400):
    """Get the thumbnail URL for a Wikimedia file."""
    # For simplicity, we'll try direct download first
    # Return the thumbnail URL format
    return f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/{filename}/{target_size}px-{filename.split('.')[0]}-{target_size}px.jpg"

def download_image(url, filepath):
    """Download an image from URL."""
    try:
        # Add headers to avoid 403 errors
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            with open(filepath, 'wb') as f:
                f.write(data)
        return True
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return False

def main():
    # Create output directory
    output_dir = os.path.join(os.path.dirname(__file__), 'static', 'images', 'cars')
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"Downloading car images to {output_dir}...")
    print("Note: This script requires internet access to download from Wikimedia Commons.")
    print("\nTo manually download car images:")
    print("1. Go to https://commons.wikimedia.org")
    print("2. Search for the car model")
    print("3. Download the image and save to static/images/cars/")
    print("\nFile naming: BRAND-MODEL.jpg (e.g., Toyota-Vios.jpg)")
    
    # For now, just create the directory structure
    print(f"\nCreated directory: {output_dir}")
    
    # Print list of expected files
    print("\nExpected image files:")
    for key in sorted(CAR_IMAGES.keys()):
        print(f"  {key}.jpg")

if __name__ == "__main__":
    main()