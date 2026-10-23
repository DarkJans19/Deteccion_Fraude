export default function RadialGlowBackground() {
  return (
    <div className="fixed inset-0 -z-10 bg-black">
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            "radial-gradient(circle 560px at 50% 200px, rgba(139,124,248,0.35), transparent)",
        }}
      />
    </div>
  );
}
