import { defineConfig } from "vite";

export default defineConfig({
  build: {
    target: "es2020",
    cssCodeSplit: true,
    // three.js خودش ~۱۴۷KB (gzip) است و فقط با نزدیک‌شدن به بخش انیمیشن بارگذاری می‌شود
    chunkSizeWarningLimit: 700,
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        // three و gsap در چانک جدا؛ فقط وقتی بخش انیمیشن نزدیک دید کاربر شد بارگذاری می‌شوند
        manualChunks(id) {
          if (id.includes("node_modules/three")) return "three";
          if (id.includes("node_modules/gsap")) return "gsap";
        },
      },
    },
  },
  server: { port: 5173 },
});
