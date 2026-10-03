import warnings
warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import f1_score
from sklearn.ensemble import RandomForestClassifier

ROOT = Path("data/agri")
TRAINING_FILE = ROOT / "agri_training.csv"
df = pd.read_csv(TRAINING_FILE, parse_dates=["period_end", "image_date"])

CLASSES = ["healthy", "watch", "stressed", "severe"]
CLASS_TO_ID = {label: idx for idx, label in enumerate(CLASSES)}
df = df[df["target_stress_class"].isin(CLASSES)].copy()
df = df[df["stress_class_now"].isin(CLASSES)].copy()
df["season_year"] = df["season_year"].astype(int)

# Use the calculate_safe_features from 04_train_model.py
# Wait, I'll just run it as a standalone by copy-pasting the relevant parts.
