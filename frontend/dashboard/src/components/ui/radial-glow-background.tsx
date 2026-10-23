export default function RadialGlowBackground() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-[#020617]">
      <div
        className="absolute inset-0 z-0"
        style={{
          backgroundImage: `radial-gradient(circle 500px at 50% 100px, rgba(249,115,22,0.4), transparent)`,
        }}
      />
    </div>
  );
}
