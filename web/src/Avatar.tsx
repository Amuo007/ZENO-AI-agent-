import { useEffect, useRef, type CSSProperties, type MutableRefObject } from 'react'
import { Clock, Eye, MessageCircleQuestion } from 'lucide-react'
import { useIsDark } from './theme'

export type Mood = 'offline' | 'idle' | 'listening' | 'thinking' | 'speaking'

type PoseName = 'idle' | 'listening' | 'speaking'

/** An eye's box in the art's pixels (540×606), and the visor's tone around it. */
type EyeBox = { x0: number; x1: number; y0: number; y1: number; tint: string }

/**
 * The three poses. The speaking pose is drawn without its mouth: a live one is drawn on top,
 * opening and closing with the voice. Head tilt is in degrees, measured from the eyes.
 */
const POSES: Record<PoseName, { src: string; tilt: number; eyes: [EyeBox, EyeBox] }> = {
  idle: {
    src: '/avatars/zeno-idle.webp',
    tilt: 14.8,
    eyes: [
      { x0: 215, x1: 295, y0: 224, y1: 273, tint: '#050505' },
      { x0: 363, x1: 424, y0: 262, y1: 308, tint: '#0b0b0b' },
    ],
  },
  listening: {
    src: '/avatars/zeno-listening.webp',
    tilt: -13.6,
    eyes: [
      { x0: 261, x1: 308, y0: 240, y1: 303, tint: '#0c0c0c' },
      { x0: 405, x1: 445, y0: 205, y1: 270, tint: '#161616' },
    ],
  },
  speaking: {
    src: '/avatars/zeno-speaking-face.webp',
    tilt: -10.2,
    eyes: [
      { x0: 211, x1: 283, y0: 249, y1: 292, tint: '#080808' },
      { x0: 357, x1: 416, y0: 226, y1: 265, tint: '#0d0d0d' },
    ],
  },
}
// Where the speaking pose's mouth was: the middle of its top edge.
const MOUTH = { x: 319.5, y: 283.5 }
const ART_WIDTH = 540
const ART_HEIGHT = 606

/**
 * The mouth for an opening between 0 (a slim smile) and 1 (wide open): a flat-topped "D" like
 * the original art's, whose lower lip drops as the voice gets louder.
 */
function mouthPath(open: number): string {
  const half = 24 - open * 4
  const dip = 2.2 - open * 1.2
  // A cubic curve reaches 3/4 of its control points' depth.
  const depth = (3 + open * 32) / 0.75
  return `M ${-half} 0 Q 0 ${dip} ${half} 0 C ${half} ${depth} ${-half} ${depth} ${-half} 0 Z`
}

/**
 * Eases a 0–1 audio level (written to a ref by audio events) into a CSS variable every frame,
 * and calls onFrame with the raw level so a caller can drive its own animation from it.
 */
function useLevelVariable(
  element: MutableRefObject<HTMLElement | null>,
  level?: MutableRefObject<number>,
  onFrame?: (target: number) => void,
) {
  const frameHandler = useRef(onFrame)
  frameHandler.current = onFrame
  useEffect(() => {
    let frame = 0
    let smoothed = 0
    const tick = () => {
      const target = Math.min(1, Math.max(0, level?.current ?? 0))
      // Rise fast, fall slowly: reads like a needle on an analog meter.
      smoothed += (target - smoothed) * (target > smoothed ? 0.35 : 0.08)
      element.current?.style.setProperty('--level', smoothed.toFixed(3))
      frameHandler.current?.(target)
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [element, level])
}

/** Lids that drop over a pose's eyes for a moment every few seconds. */
function Lids({ pose, delay }: { pose: PoseName; delay: string }) {
  const { tilt, eyes } = POSES[pose]
  const margin = 6
  return (
    <>
      {eyes.map((eye, i) => {
        const cx = (eye.x0 + eye.x1) / 2
        const cy = (eye.y0 + eye.y1) / 2
        return (
          <g key={i} transform={`rotate(${tilt} ${cx} ${cy})`}>
            <rect
              className="zeno-lid"
              style={{ animationDelay: delay }}
              x={eye.x0 - margin}
              y={eye.y0 - 3}
              width={eye.x1 - eye.x0 + margin * 2}
              height={eye.y1 - eye.y0 + margin + 3}
              rx={10}
              fill={eye.tint}
            />
          </g>
        )
      })}
    </>
  )
}

/**
 * ZENO, the boss: three poses that settle into one another with the conversation (idle,
 * listening, speaking), hovering gently, blinking now and then, and moving its mouth with
 * its voice.
 */
export function ZenoAvatar({
  mood,
  level,
  size = 260,
  className = '',
}: {
  mood: Mood
  level?: MutableRefObject<number>
  size?: number
  className?: string
}) {
  const root = useRef<HTMLDivElement>(null)
  const mouth = useRef<SVGPathElement>(null)
  const open = useRef(0)
  const art: PoseName = mood === 'listening' ? 'listening' : mood === 'speaking' ? 'speaking' : 'idle'
  const speaking = art === 'speaking'

  useLevelVariable(root, level, (target) => {
    // The mouth follows syllables: it opens quickly and closes almost as quickly.
    const goal = speaking ? Math.min(1, target * 1.25) : 0
    open.current += (goal - open.current) * (goal > open.current ? 0.55 : 0.32)
    mouth.current?.setAttribute('d', mouthPath(open.current))
  })

  return (
    <div
      ref={root}
      className={`relative select-none ${className}`}
      style={{ width: size * 0.89, height: size, '--level': 0 } as CSSProperties}
      aria-hidden
    >
      {/* Aura: breathes while thinking, swells with the voice. */}
      <div
        className={`absolute left-1/2 top-[42%] aspect-square w-[78%] rounded-full bg-[var(--color-glow)] blur-2xl ${
          mood === 'thinking' ? 'animate-breathe' : ''
        }`}
        style={{
          transform: 'translate(-50%, -50%) scale(calc(0.85 + var(--level) * 0.45))',
          opacity: mood === 'offline' ? 0.25 : 'calc(0.55 + var(--level) * 0.9)',
        }}
      />
      {mood === 'listening' && (
        <div className="absolute left-1/2 top-[40%] aspect-square w-[70%] -translate-x-1/2 -translate-y-1/2">
          <div className="absolute inset-0 rounded-full border border-[var(--color-faint)] animate-ripple" />
          <div className="absolute inset-0 rounded-full border border-[var(--color-faint)] animate-ripple [animation-delay:0.9s]" />
        </div>
      )}
      <div className={`absolute inset-0 ${mood === 'offline' ? '' : 'animate-float'}`}>
        <div
          className="absolute inset-0 origin-[50%_70%] transition-[filter] duration-500"
          style={{
            transform:
              'scale(calc(1 + var(--level) * 0.035)) translateY(calc(var(--level) * -5px)) rotate(calc(var(--level) * -1.2deg))',
            filter: mood === 'offline' ? 'saturate(0) brightness(0.92)' : undefined,
          }}
        >
          {(Object.keys(POSES) as PoseName[]).map((key, i) => {
            const active = key === art
            return (
              <div
                key={key}
                className="zeno-pose avatar-art absolute inset-0"
                style={{
                  opacity: active ? 1 : 0,
                  transform: active ? 'none' : 'scale(0.975) translateY(4px)',
                }}
              >
                <img src={POSES[key].src} alt="" draggable={false} className="absolute inset-0 h-full w-full object-contain" />
                <svg
                  viewBox={`0 0 ${ART_WIDTH} ${ART_HEIGHT}`}
                  preserveAspectRatio="xMidYMid meet"
                  className="absolute inset-0 h-full w-full overflow-visible"
                >
                  <defs>
                    <filter id={`zeno-glow-${key}`} x="-60%" y="-60%" width="220%" height="220%">
                      <feGaussianBlur stdDeviation="3.2" result="soft" />
                      <feMerge>
                        <feMergeNode in="soft" />
                        <feMergeNode in="soft" />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                  </defs>
                  {mood !== 'offline' && <Lids pose={key} delay={`${-1.3 * i}s`} />}
                  {key === 'speaking' && (
                    <g transform={`translate(${MOUTH.x} ${MOUTH.y}) rotate(${POSES.speaking.tilt})`}>
                      <path ref={mouth} d={mouthPath(0)} fill="#f6f6f8" filter={`url(#zeno-glow-${key})`} />
                    </g>
                  )}
                </svg>
              </div>
            )
          })}
        </div>
      </div>
      {/* Ground shadow, shrinking as ZENO rises. */}
      <div
        className="absolute bottom-[1%] left-1/2 h-[4%] w-[42%] rounded-[50%] bg-[var(--color-shadow)] blur-md"
        style={{ transform: 'translateX(-50%) scaleX(calc(1 - var(--level) * 0.2))' }}
      />
    </div>
  )
}

/**
 * An analog, oscilloscope-like trace of the live voice: three sine lines whose amplitude
 * follows the level, flat-lining (with a faint breath) when nobody is talking.
 */
export function VoiceWave({
  level,
  active,
  width = 240,
  height = 44,
}: {
  level: MutableRefObject<number>
  active: boolean
  width?: number
  height?: number
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const dark = useIsDark()

  useEffect(() => {
    const element = canvas.current
    const context = element?.getContext('2d')
    if (!element || !context) return
    const ratio = window.devicePixelRatio || 1
    element.width = width * ratio
    element.height = height * ratio
    context.scale(ratio, ratio)
    const ink = dark ? '245,245,247' : '29,29,31'
    let frame = 0
    let phase = 0
    let amplitude = 0
    const draw = () => {
      const target = active ? 0.06 + Math.min(1, level.current) * 0.94 : 0.03
      amplitude += (target - amplitude) * (target > amplitude ? 0.3 : 0.07)
      phase += 0.09 + amplitude * 0.14
      context.clearRect(0, 0, width, height)
      const lines = [
        { speed: 1, freq: 1.6, alpha: 0.9, weight: 1.6 },
        { speed: -0.7, freq: 2.3, alpha: 0.45, weight: 1.2 },
        { speed: 1.4, freq: 3.1, alpha: 0.25, weight: 1 },
      ]
      for (const line of lines) {
        context.beginPath()
        for (let x = 0; x <= width; x += 2) {
          const t = x / width
          // Taper at both ends, like a scope trace fading at the edges.
          const envelope = Math.sin(Math.PI * t) ** 1.6
          const y =
            height / 2 +
            Math.sin(t * Math.PI * 2 * line.freq + phase * line.speed) * envelope * amplitude * (height / 2 - 3)
          if (x === 0) context.moveTo(x, y)
          else context.lineTo(x, y)
        }
        context.strokeStyle = `rgba(${ink},${line.alpha})`
        context.lineWidth = line.weight
        context.lineCap = 'round'
        context.stroke()
      }
      frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [level, active, width, height, dark])

  return <canvas ref={canvas} style={{ width, height }} aria-hidden />
}

export type BitState = 'idle' | 'working' | 'waiting' | 'asking' | 'watching' | 'sleeping' | 'done' | 'failed'

const BIT_BADGES: Partial<Record<BitState, { icon: typeof Eye; label: string }>> = {
  asking: { icon: MessageCircleQuestion, label: 'Has a question' },
  watching: { icon: Eye, label: 'Keeping watch' },
  sleeping: { icon: Clock, label: 'Waiting' },
}

/**
 * A Bit, the worker: bobs while it works, rests (dimmed) while idle, and wears a small badge
 * when it has a question for you, keeps watch, or waits for a time.
 */
export function BitAvatar({
  slot,
  state = 'idle',
  size = 72,
  className = '',
}: {
  slot?: number
  state?: BitState
  size?: number
  className?: string
}) {
  const working = state === 'working' || state === 'asking'
  const badge = BIT_BADGES[state]
  const BadgeIcon = badge?.icon
  const dim = state === 'idle' || state === 'waiting'
  return (
    <div className={`relative shrink-0 select-none ${className}`} style={{ width: size, height: size }} aria-hidden>
      {(working || state === 'watching') && (
        <div
          className={`absolute inset-[12%] rounded-full blur-xl animate-breathe ${
            state === 'asking' ? 'bg-warning/30' : 'bg-[var(--color-glow)]'
          }`}
        />
      )}
      <img
        src="/avatars/bit.webp"
        alt=""
        draggable={false}
        className={`avatar-art absolute inset-0 h-full w-full object-contain transition duration-500 ${
          working ? 'animate-bob' : state === 'watching' || state === 'sleeping' ? 'animate-float' : ''
        } ${dim ? 'opacity-55 saturate-0' : ''} ${state === 'sleeping' ? 'opacity-80' : ''} ${state === 'failed' ? 'opacity-70' : ''}`}
      />
      {BadgeIcon && (
        <span
          title={badge.label}
          className={`animate-badge absolute -right-0.5 -top-0.5 flex items-center justify-center rounded-full border-2 border-surface ${
            state === 'asking' ? 'bg-warning text-white' : 'bg-ink text-canvas'
          }`}
          style={{ width: Math.max(18, size * 0.3), height: Math.max(18, size * 0.3) }}
        >
          <BadgeIcon size={Math.max(11, size * 0.17)} strokeWidth={2.4} />
        </span>
      )}
      {slot !== undefined && (
        <span className="absolute -bottom-0.5 -right-0.5 flex h-[22px] min-w-[22px] items-center justify-center rounded-full border-2 border-surface bg-ink px-1 font-mono text-[10px] font-bold text-canvas">
          {String(slot).padStart(2, '0')}
        </span>
      )}
    </div>
  )
}
