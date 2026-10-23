// MongoDB local: el contenedor ejecuta este archivo al inicializar un volumen nuevo.
// El backend vuelve a sincronizar estos mismos validadores al arrancar.
const schemas = JSON.parse(require("fs").readFileSync(
  process.env.FRAUD_SCHEMA_PATH || "/docker-entrypoint-initdb.d/schema.json", "utf8",
));
const database = db.getSiblingDB(process.env.MONGO_INITDB_DATABASE || "fraud_detection");
for (const [name, schema] of Object.entries(schemas)) {
  const options = { validator: { $jsonSchema: schema }, validationLevel: "strict", validationAction: "error" };
  if (database.getCollectionInfos({ name }).length) {
    const result = database.runCommand({ collMod: name, ...options });
    if (!result.ok) throw new Error(`No se pudo actualizar el validador de ${name}`);
  } else {
    database.createCollection(name, options);
  }
}
function ensureIndex(collection, keys, options) {
  const existing = collection.getIndexes().some((index) =>
    JSON.stringify(index.key) === JSON.stringify(keys) && Boolean(index.unique) === Boolean(options.unique) &&
    !index.partialFilterExpression && !index.sparse && !index.collation);
  if (!existing) collection.createIndex(keys, options);
}
ensureIndex(database.fraud_reports, { hashCode: 1 }, { unique: true, name: "report_hash_unique" });
ensureIndex(database.fraud_reports, { date: -1, _id: -1 }, { name: "report_date" });
ensureIndex(database.fraud_reports, { location: "2dsphere" }, { name: "report_location" });
ensureIndex(database.fraud_report_events, { reportId: 1, date: 1, _id: 1 }, { name: "report_history" });
