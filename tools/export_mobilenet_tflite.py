"""
Exporta MobileNetV3-Small (ImageNet, sin cabeza, pooling promedio) a TFLite
para usarlo como extractor de embeddings en el teléfono (opcional).

Requiere TensorFlow en el PC (no en el teléfono):
    pip install tensorflow
    python tools/export_mobilenet_tflite.py

Genera:
    assets/models/mobilenet_v3_small_embed.tflite   (~2-3 MB, cuantización dinámica)
    assets/models/model.json                        (metadatos de preprocesamiento)

Luego agregue `tflite-runtime` a `requirements` en buildozer.spec.
ai_classifier.get_extractor() lo detecta automáticamente; en el módulo de
calibración use «Re-entrenar» para recalcular las referencias con el nuevo extractor.
"""
import json
import os

import tensorflow as tf

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "models")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    model = tf.keras.applications.MobileNetV3Small(
        input_shape=(224, 224, 3), include_top=False, pooling="avg",
        weights="imagenet", include_preprocessing=True)  # espera píxeles 0-255
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    path = os.path.join(OUT, "mobilenet_v3_small_embed.tflite")
    with open(path, "wb") as f:
        f.write(converter.convert())
    with open(os.path.join(OUT, "model.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "MobileNetV3Small-imagenet", "input_scale": "0_255",
                   "input_size": 224, "embedding_dim": int(model.output_shape[-1])}, f, indent=2)
    print(f"Modelo exportado: {path} ({os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    main()
