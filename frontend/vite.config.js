import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // dev 전용: 배포는 nginx(frontend/nginx.conf)가 /api를 프록시하므로 이 설정은 빌드 결과에 영향이 없다.
  // 로컬에서 `npm run dev`로 띄울 때만 5173 → 도커 api 컨테이너(5000)로 넘겨준다.
  server: {
    proxy: {
      '/api': 'http://localhost:5000',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/setupTests.js',
    globals: true,
  },
})
