import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { PipecatClient } from '@pipecat-ai/client-js'
import { PipecatClientAudio, PipecatClientProvider } from '@pipecat-ai/client-react'
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport'
import './index.css'
import App from './App.tsx'
import { AuthGate } from './auth.tsx'

const client = new PipecatClient({
  transport: new SmallWebRTCTransport(),
  enableMic: true,
  enableCam: false,
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthGate>
      <PipecatClientProvider client={client}>
        <App />
        <PipecatClientAudio />
      </PipecatClientProvider>
    </AuthGate>
  </StrictMode>,
)
