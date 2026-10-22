"""Entrenamiento, evaluacion e inferencia del autoencoder de URLs."""

from __future__ import annotations

import csv
import json
import os
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .features import FEATURE_NAMES, LEXICAL_FEATURE_NAMES, extract_features
from .preprocessing import LABEL_LEGITIMATE, normalize_url


def _dependencies():
    try:
        # Evita que mensajes informativos internos de TensorFlow se mezclen con
        # la salida JSON de los comandos de entrenamiento e inferencia.
        os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
        import numpy as np
        import tensorflow as tf
        tf.get_logger().setLevel("ERROR")
    except ImportError as error:
        raise RuntimeError(
            "Faltan dependencias del modelo. Instala: "
            "python -m pip install -r requirements-url.txt"
        ) from error
    return np, tf


def read_records(path: Path) -> Iterable[tuple[str, int]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if not {"url", "label"}.issubset(reader.fieldnames or ()):
            raise ValueError(f"{path}: se esperaban las columnas url y label")
        for number, row in enumerate(reader, start=2):
            try:
                label = int(row["label"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}, registro {number}: label invalido") from error
            if label not in {0, 1}:
                raise ValueError(f"{path}, registro {number}: label debe ser 0 o 1")
            yield row["url"], label


def reservoir_sample(
    records: Iterable[tuple[str, int]],
    limit: int | None,
    seed: int,
    label: int | None = None,
) -> list[tuple[str, int]]:
    """Muestreo uniforme que no necesita cargar todo el CSV en memoria."""

    rng = random.Random(seed)
    sample: list[tuple[str, int]] = []
    seen = 0
    for record in records:
        if label is not None and record[1] != label:
            continue
        seen += 1
        if limit is None or len(sample) < limit:
            sample.append(record)
        else:
            replacement = rng.randrange(seen)
            if replacement < limit:
                sample[replacement] = record
    return sample


def feature_matrix(records: Iterable[tuple[str, int]]):
    np, _ = _dependencies()
    rows = list(records)
    features = np.asarray([extract_features(url) for url, _ in rows], dtype="float32")
    labels = np.asarray([label for _, label in rows], dtype="int8")
    return features, labels


def build_autoencoder(input_size: int, seed: int):
    _, tf = _dependencies()
    tf.keras.utils.set_random_seed(seed)
    inputs = tf.keras.Input(shape=(input_size,), name="url_features")
    value = tf.keras.layers.Dense(128, activation="relu")(inputs)
    value = tf.keras.layers.Dense(64, activation="relu")(value)
    bottleneck = tf.keras.layers.Dense(16, activation="relu", name="bottleneck")(value)
    value = tf.keras.layers.Dense(64, activation="relu")(bottleneck)
    value = tf.keras.layers.Dense(128, activation="relu")(value)
    outputs = tf.keras.layers.Dense(input_size, name="reconstruction")(value)
    model = tf.keras.Model(inputs, outputs, name="url_autoencoder")
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3), loss="mse")
    return model


def reconstruction_errors(model, features, batch_size: int = 1024):
    np, _ = _dependencies()
    reconstructed = model.predict(features, batch_size=batch_size, verbose=0)
    return np.mean(np.square(features - reconstructed), axis=1)


def classification_metrics(labels, errors, threshold: float) -> dict[str, float | int]:
    np, _ = _dependencies()
    predicted = errors > threshold
    actual = labels.astype(bool)
    tp = int(np.sum(predicted & actual))
    tn = int(np.sum(~predicted & ~actual))
    fp = int(np.sum(predicted & ~actual))
    fn = int(np.sum(~predicted & actual))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "samples": int(len(labels)),
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "accuracy": (tp + tn) / len(labels) if len(labels) else 0.0,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def train(
    dataset_dir: Path,
    output_dir: Path,
    max_train_samples: int = 100_000,
    max_evaluation_samples: int = 100_000,
    epochs: int = 40,
    batch_size: int = 512,
    false_positive_rate: float = 0.01,
    seed: int = 42,
) -> dict[str, object]:
    np, tf = _dependencies()
    if not 0 < false_positive_rate < 1:
        raise ValueError("false_positive_rate debe estar entre 0 y 1")
    if max_train_samples <= 0 or max_evaluation_samples <= 0:
        raise ValueError("Los limites de muestras deben ser positivos")
    if epochs <= 0 or batch_size <= 0:
        raise ValueError("epochs y batch_size deben ser positivos")

    train_rows = reservoir_sample(
        read_records(dataset_dir / "train.csv"),
        max_train_samples,
        seed,
        label=LABEL_LEGITIMATE,
    )
    validation_rows = reservoir_sample(
        read_records(dataset_dir / "validation.csv"),
        max_evaluation_samples,
        seed + 1,
    )
    test_rows = reservoir_sample(
        read_records(dataset_dir / "test.csv"),
        max_evaluation_samples,
        seed + 2,
    )
    if not train_rows:
        raise ValueError("No hay URLs legitimas en train.csv")

    x_train, _ = feature_matrix(train_rows)
    x_validation, y_validation = feature_matrix(validation_rows)
    x_test, y_test = feature_matrix(test_rows)

    mean = x_train.mean(axis=0)
    std = x_train.std(axis=0)
    std = np.where(std < 1e-7, 1.0, std)

    def scale(values):
        return np.clip((values - mean) / std, -10.0, 10.0)

    x_train = scale(x_train)
    x_validation = scale(x_validation)
    x_test = scale(x_test)
    legitimate_validation = x_validation[y_validation == LABEL_LEGITIMATE]
    if not len(legitimate_validation):
        raise ValueError("No hay URLs legitimas en validation.csv para calibrar el umbral")

    model = build_autoencoder(len(FEATURE_NAMES), seed)
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=5, restore_best_weights=True
        )
    ]
    history = model.fit(
        x_train,
        x_train,
        validation_data=(legitimate_validation, legitimate_validation),
        epochs=epochs,
        batch_size=batch_size,
        shuffle=True,
        callbacks=callbacks,
        verbose=2,
    )

    validation_errors = reconstruction_errors(model, x_validation, batch_size)
    legitimate_errors = validation_errors[y_validation == LABEL_LEGITIMATE]
    threshold = float(np.quantile(legitimate_errors, 1.0 - false_positive_rate))
    test_errors = reconstruction_errors(model, x_test, batch_size)

    metadata: dict[str, object] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_type": "dense_autoencoder",
        "feature_schema_version": 1,
        "label_convention": {"0": "legitimate", "1": "phishing"},
        "feature_names": list(FEATURE_NAMES),
        "lexical_feature_count": len(LEXICAL_FEATURE_NAMES),
        "mean": mean.astype(float).tolist(),
        "std": std.astype(float).tolist(),
        "threshold": threshold,
        "threshold_calibration": {
            "method": "legitimate_validation_quantile",
            "target_false_positive_rate": false_positive_rate,
            "legitimate_samples": int(len(legitimate_errors)),
        },
        "training": {
            "samples": len(train_rows),
            "epochs_requested": epochs,
            "epochs_completed": len(history.history["loss"]),
            "batch_size": batch_size,
            "seed": seed,
            "final_loss": float(history.history["loss"][-1]),
            "final_validation_loss": float(history.history["val_loss"][-1]),
        },
        "validation_metrics": classification_metrics(
            y_validation, validation_errors, threshold
        ),
        "test_metrics": classification_metrics(y_test, test_errors, threshold),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    model.save(output_dir / "model.keras")
    with (output_dir / "metadata.json").open("w", encoding="utf-8") as target:
        json.dump(metadata, target, ensure_ascii=False, indent=2)
        target.write("\n")
    trusted_reference = dataset_dir / "trusted_reference.json"
    if trusted_reference.is_file():
        shutil.copyfile(trusted_reference, output_dir / trusted_reference.name)
    return metadata


def predict(url: str, model_dir: Path) -> dict[str, object]:
    np, tf = _dependencies()
    normalized = normalize_url(url)
    if normalized is None:
        raise ValueError("La URL no es HTTP(S) valida o supera 2048 caracteres")

    with (model_dir / "metadata.json").open(encoding="utf-8") as source:
        metadata = json.load(source)
    if metadata.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("Los rasgos del modelo no coinciden con esta version del codigo")

    model = tf.keras.models.load_model(model_dir / "model.keras")
    raw = np.asarray([extract_features(normalized)], dtype="float32")
    mean = np.asarray(metadata["mean"], dtype="float32")
    std = np.asarray(metadata["std"], dtype="float32")
    scaled = np.clip((raw - mean) / std, -10.0, 10.0)
    reconstructed = model.predict(scaled, verbose=0)
    residuals = np.square(scaled - reconstructed)[0]
    score = float(np.mean(residuals))
    threshold = float(metadata["threshold"])

    lexical_count = len(LEXICAL_FEATURE_NAMES)
    top_indices = np.argsort(residuals[:lexical_count])[-5:][::-1]
    signals = [
        {
            "feature": LEXICAL_FEATURE_NAMES[int(index)],
            "reconstruction_error": float(residuals[int(index)]),
        }
        for index in top_indices
    ]
    return {
        "url": url,
        "normalized_url": normalized,
        "classification": "suspicious" if score > threshold else "legitimate_pattern",
        "is_suspicious": score > threshold,
        "anomaly_score": score,
        "threshold": threshold,
        "score_to_threshold_ratio": score / threshold if threshold else None,
        "main_anomalous_features": signals,
        "warning": "El resultado es una senal estadistica, no una prueba definitiva de phishing.",
    }
