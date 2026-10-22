"""Detector hibrido: rasgos URL + autoencoder + clasificador supervisado."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .autoencoder import (
    _dependencies,
    classification_metrics,
    feature_matrix,
    read_records,
    reservoir_sample,
)
from .features import FEATURE_NAMES, LEXICAL_FEATURE_NAMES, extract_features
from .domains import strip_www
from .preprocessing import normalize_url


def _scaled_features(raw, autoencoder_metadata):
    np, _ = _dependencies()
    mean = np.asarray(autoencoder_metadata["mean"], dtype="float32")
    std = np.asarray(autoencoder_metadata["std"], dtype="float32")
    return np.clip((raw - mean) / std, -10.0, 10.0)


def _hybrid_matrix(raw, autoencoder, autoencoder_metadata, anomaly_mean, anomaly_std):
    np, _ = _dependencies()
    scaled = _scaled_features(raw, autoencoder_metadata)
    reconstruction = autoencoder.predict(scaled, batch_size=1024, verbose=0)
    anomaly = np.mean(np.square(scaled - reconstruction), axis=1)
    normalized_anomaly = (np.log1p(anomaly) - anomaly_mean) / max(anomaly_std, 1e-7)
    return np.column_stack((scaled, normalized_anomaly)), anomaly


def _threshold_at_fpr(labels, probabilities, maximum_fpr: float) -> float:
    np, _ = _dependencies()
    legitimate = probabilities[labels == 0]
    if not len(legitimate):
        raise ValueError("No hay URLs legitimas para calibrar el umbral")
    return float(np.quantile(legitimate, 1.0 - maximum_fpr, method="higher"))


def _metrics(labels, probabilities, threshold: float) -> dict[str, float | int]:
    result = classification_metrics(labels, probabilities, threshold)
    negatives = int((labels == 0).sum())
    result["false_positive_rate"] = (
        result["false_positive"] / negatives if negatives else 0.0
    )
    return result


def build_classifier(input_size: int, seed: int):
    _, tf = _dependencies()
    tf.keras.utils.set_random_seed(seed)
    inputs = tf.keras.Input(shape=(input_size,), name="hybrid_url_features")
    value = tf.keras.layers.Dense(256, activation="relu")(inputs)
    value = tf.keras.layers.BatchNormalization()(value)
    value = tf.keras.layers.Dropout(0.30)(value)
    value = tf.keras.layers.Dense(128, activation="relu")(value)
    value = tf.keras.layers.Dropout(0.20)(value)
    value = tf.keras.layers.Dense(64, activation="relu")(value)
    output = tf.keras.layers.Dense(1, activation="sigmoid", name="phishing_probability")(value)
    model = tf.keras.Model(inputs, output, name="hybrid_url_classifier")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=8e-4),
        loss="binary_crossentropy",
        metrics=[
            tf.keras.metrics.AUC(name="roc_auc"),
            tf.keras.metrics.AUC(curve="PR", name="pr_auc"),
        ],
    )
    return model


def train_classifier(
    dataset_dir: Path,
    model_dir: Path,
    max_train_samples: int = 200_000,
    max_evaluation_samples: int = 100_000,
    epochs: int = 30,
    batch_size: int = 512,
    decision_fpr: float = 0.01,
    review_fpr: float = 0.05,
    seed: int = 42,
) -> dict[str, object]:
    np, tf = _dependencies()
    if not 0 < decision_fpr < review_fpr < 1:
        raise ValueError("Se requiere 0 < decision_fpr < review_fpr < 1")

    with (model_dir / "metadata.json").open(encoding="utf-8") as source:
        autoencoder_metadata = json.load(source)
    if autoencoder_metadata.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("Reentrena el autoencoder: el esquema de rasgos ha cambiado")
    autoencoder = tf.keras.models.load_model(model_dir / "model.keras")

    train_rows = reservoir_sample(
        read_records(dataset_dir / "train.csv"), max_train_samples, seed
    )
    validation_rows = reservoir_sample(
        read_records(dataset_dir / "validation.csv"), max_evaluation_samples, seed + 1
    )
    test_rows = reservoir_sample(
        read_records(dataset_dir / "test.csv"), max_evaluation_samples, seed + 2
    )
    raw_train, y_train = feature_matrix(train_rows)
    raw_validation, y_validation = feature_matrix(validation_rows)
    raw_test, y_test = feature_matrix(test_rows)

    scaled_train = _scaled_features(raw_train, autoencoder_metadata)
    reconstructed_train = autoencoder.predict(scaled_train, batch_size=1024, verbose=0)
    train_anomaly = np.mean(np.square(scaled_train - reconstructed_train), axis=1)
    anomaly_log = np.log1p(train_anomaly)
    anomaly_mean = float(anomaly_log.mean())
    anomaly_std = float(anomaly_log.std()) or 1.0
    x_train = np.column_stack(
        (scaled_train, (anomaly_log - anomaly_mean) / anomaly_std)
    )
    x_validation, _ = _hybrid_matrix(
        raw_validation, autoencoder, autoencoder_metadata, anomaly_mean, anomaly_std
    )
    x_test, _ = _hybrid_matrix(
        raw_test, autoencoder, autoencoder_metadata, anomaly_mean, anomaly_std
    )

    counts = np.bincount(y_train.astype("int32"), minlength=2)
    class_weight = {
        index: len(y_train) / (2.0 * count) for index, count in enumerate(counts) if count
    }
    classifier = build_classifier(x_train.shape[1], seed)
    history = classifier.fit(
        x_train,
        y_train,
        validation_data=(x_validation, y_validation),
        epochs=epochs,
        batch_size=batch_size,
        class_weight=class_weight,
        callbacks=[
            tf.keras.callbacks.EarlyStopping(
                monitor="val_pr_auc", mode="max", patience=5, restore_best_weights=True
            )
        ],
        shuffle=True,
        verbose=2,
    )

    validation_probability = classifier.predict(
        x_validation, batch_size=1024, verbose=0
    ).reshape(-1)
    test_probability = classifier.predict(x_test, batch_size=1024, verbose=0).reshape(-1)
    decision_threshold = _threshold_at_fpr(
        y_validation, validation_probability, decision_fpr
    )
    review_threshold = _threshold_at_fpr(y_validation, validation_probability, review_fpr)

    metadata: dict[str, object] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_type": "supervised_hybrid_dense_classifier",
        "feature_names": list(FEATURE_NAMES) + ["autoencoder_anomaly_score"],
        "decision_threshold": decision_threshold,
        "review_threshold": review_threshold,
        "calibration": {
            "decision_maximum_fpr": decision_fpr,
            "review_maximum_fpr": review_fpr,
        },
        "anomaly_log_mean": anomaly_mean,
        "anomaly_log_std": anomaly_std,
        "training": {
            "samples": len(train_rows),
            "class_counts": {"legitimate": int(counts[0]), "phishing": int(counts[1])},
            "epochs_completed": len(history.history["loss"]),
            "seed": seed,
        },
        "validation_metrics": _metrics(
            y_validation, validation_probability, decision_threshold
        ),
        "test_metrics": _metrics(y_test, test_probability, decision_threshold),
        "test_review_metrics": _metrics(y_test, test_probability, review_threshold),
    }
    classifier.save(model_dir / "classifier.keras")
    with (model_dir / "classifier_metadata.json").open("w", encoding="utf-8") as target:
        json.dump(metadata, target, ensure_ascii=False, indent=2)
        target.write("\n")
    return metadata


def predict(url: str, model_dir: Path) -> dict[str, object]:
    np, tf = _dependencies()
    normalized = normalize_url(url)
    if normalized is None:
        raise ValueError("La URL no es HTTP(S) valida o supera 2048 caracteres")

    with (model_dir / "metadata.json").open(encoding="utf-8") as source:
        autoencoder_metadata = json.load(source)
    with (model_dir / "classifier_metadata.json").open(encoding="utf-8") as source:
        classifier_metadata = json.load(source)
    if autoencoder_metadata.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("El modelo no coincide con esta version de los rasgos")

    trusted_reference_path = model_dir / "trusted_reference.json"
    official_host_match = False
    official_base_match = False
    if trusted_reference_path.is_file():
        with trusted_reference_path.open(encoding="utf-8") as source:
            trusted_reference = json.load(source)
        trusted_hosts = set(trusted_reference.get("hosts", []))
        parsed = urlsplit(normalized)
        official_host_match = strip_www(parsed.hostname or "") in trusted_hosts
        official_base_match = bool(
            official_host_match
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and parsed.username is None
        )

    autoencoder = tf.keras.models.load_model(model_dir / "model.keras")
    classifier = tf.keras.models.load_model(model_dir / "classifier.keras")
    raw = np.asarray([extract_features(normalized)], dtype="float32")
    hybrid, anomaly = _hybrid_matrix(
        raw,
        autoencoder,
        autoencoder_metadata,
        float(classifier_metadata["anomaly_log_mean"]),
        float(classifier_metadata["anomaly_log_std"]),
    )
    probability = float(classifier.predict(hybrid, verbose=0)[0, 0])
    decision_threshold = float(classifier_metadata["decision_threshold"])
    review_threshold = float(classifier_metadata["review_threshold"])
    if probability > decision_threshold:
        risk = "high"
        classification = "suspicious"
    elif probability > review_threshold:
        risk = "review"
        classification = "uncertain"
    else:
        risk = "low"
        classification = "likely_legitimate"

    return {
        "url": url,
        "normalized_url": normalized,
        "classification": classification,
        "risk_level": risk,
        "is_suspicious": risk == "high",
        "phishing_probability": probability,
        "decision_threshold": decision_threshold,
        "review_threshold": review_threshold,
        "autoencoder_anomaly_score": float(anomaly[0]),
        "superfinanciera_host_match": official_host_match,
        "superfinanciera_base_url_match": official_base_match,
        "warning": "Es una estimacion estadistica; no demuestra que la pagina sea segura.",
    }
