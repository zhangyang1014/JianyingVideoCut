import { useCallback, useEffect, useRef, useState } from "react";
import { Film, Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import type {
  ProductionAsset,
  ProductionSelection,
  ProductionShot,
} from "@/lib/productions-api";

interface PreviewProps {
  shot: ProductionShot;
  asset?: ProductionAsset;
  voiceoverAsset?: ProductionAsset;
}

function validRange(
  selection: ProductionSelection | null,
  asset?: ProductionAsset,
) {
  return (
    !!selection &&
    !!asset &&
    selection.asset_id === asset.id &&
    Number.isFinite(selection.start) &&
    Number.isFinite(selection.end) &&
    selection.start >= 0 &&
    selection.end > selection.start &&
    selection.end <= asset.duration + 0.05
  );
}

export function ProductionShotPreview({
  shot,
  asset,
  voiceoverAsset,
}: PreviewProps) {
  if (!asset || asset.kind !== "video" || !validRange(shot.selection, asset)) {
    return (
      <figure className="flex h-full min-h-[280px] flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-background/50 p-6 text-center">
        <Film aria-hidden="true" className="h-9 w-9 text-muted-foreground" />
        <p className="text-sm font-medium">这一幕还没有可预览的画面</p>
        <p className="max-w-xs text-xs leading-relaxed text-muted-foreground">
          在右侧“画面”中回传视频，或选择素材和选段。
        </p>
      </figure>
    );
  }
  const selection = shot.selection!;
  return (
    <ClipPreview
      key={`${shot.shot_id}:${asset.id}:${selection.start}:${selection.end}:${shot.voice_mode}:${shot.voiceover?.asset_id}:${shot.voiceover?.start}:${shot.voiceover?.end}`}
      shot={shot}
      asset={asset}
      selection={selection}
      voiceoverAsset={voiceoverAsset}
    />
  );
}

function ClipPreview({
  shot,
  asset,
  selection,
  voiceoverAsset,
}: PreviewProps & {
  asset: ProductionAsset;
  selection: ProductionSelection;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const voiceReady = useRef(false);
  const frame = useRef<number | null>(null);
  const mounted = useRef(true);
  const wantsPlaying = useRef(false);
  const picturePlaying = useRef(false);
  const playRequest = useRef(0);
  const [videoError, setVideoError] = useState(false);
  const [audioError, setAudioError] = useState(false);
  const [voiceMuted, setVoiceMuted] = useState(false);
  const voiceover =
    shot.voice_mode === "voiceover" &&
    voiceoverAsset?.has_audio &&
    validRange(shot.voiceover, voiceoverAsset)
      ? shot.voiceover
      : null;
  const muteVideo =
    shot.voice_mode === "voiceover" || shot.voice_mode === "silent";

  const bindVideo = useCallback((node: HTMLVideoElement | null) => {
    if (videoRef.current && videoRef.current !== node) videoRef.current.pause();
    videoRef.current = node;
  }, []);
  const bindAudio = useCallback((node: HTMLAudioElement | null) => {
    if (audioRef.current && audioRef.current !== node) audioRef.current.pause();
    audioRef.current = node;
  }, []);
  const cancelFrame = () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      wantsPlaying.current = false;
      playRequest.current += 1;
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, []);

  const synchronizeVoice = (play = false) => {
    const video = videoRef.current;
    const audio = audioRef.current;
    if (!video || !audio || !voiceover || !voiceReady.current) return;
    const target =
      voiceover.start + Math.max(0, video.currentTime - selection.start);
    if (target >= voiceover.end) {
      audio.pause();
      return;
    }
    if (Math.abs(audio.currentTime - target) > 0.12) audio.currentTime = target;
    audio.playbackRate = video.playbackRate;
    audio.volume = video.volume;
    if (play && picturePlaying.current) {
      const request = ++playRequest.current;
      setAudioError(false);
      void audio
        .play()
        .then(() => {
          if (!mounted.current || !wantsPlaying.current) audio.pause();
        })
        .catch((error: unknown) => {
          if (
            !mounted.current ||
            request !== playRequest.current ||
            !wantsPlaying.current ||
            (error instanceof DOMException && error.name === "AbortError")
          )
            return;
          video.pause();
          setAudioError(true);
        });
    }
  };
  const enforceRange = () => {
    const video = videoRef.current;
    if (!video) return;
    const end = Number.isFinite(video.duration)
      ? Math.min(selection.end, video.duration)
      : selection.end;
    if (video.currentTime >= end) {
      video.pause();
      audioRef.current?.pause();
      wantsPlaying.current = false;
      cancelFrame();
      if (video.currentTime > end) video.currentTime = end;
    } else if (video.currentTime < selection.start) {
      video.currentTime = selection.start;
    }
    synchronizeVoice();
  };
  const animate = () => {
    enforceRange();
    if (wantsPlaying.current) frame.current = requestAnimationFrame(animate);
  };
  const soundLabel =
    shot.voice_mode === "voiceover"
      ? voiceover
        ? "已同步独立旁白 · 原画面静音"
        : "尚未选择独立旁白，当前仅预览画面"
      : shot.voice_mode === "silent"
        ? "无对白 · 画面静音"
        : asset.has_audio
          ? "播放素材原声"
          : "素材无音轨";

  return (
    <figure
      className="flex h-full min-h-0 flex-col gap-3"
      aria-label={`${shot.shot_id} 当前选段`}
    >
      <div className="relative flex min-h-[180px] flex-1 items-center justify-center overflow-hidden rounded-xl border border-border bg-black/25">
        {videoError ? (
          <div role="alert" className="space-y-3 p-6 text-center">
            <Film
              aria-hidden="true"
              className="mx-auto h-8 w-8 text-muted-foreground"
            />
            <p className="text-sm">视频未能加载，请检查素材文件后重试。</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setVideoError(false)}
            >
              重新加载画面
            </Button>
          </div>
        ) : (
          <video
            ref={bindVideo}
            aria-label={`${shot.shot_id} 画面预览`}
            src={asset.url}
            controls
            playsInline
            preload="metadata"
            muted={muteVideo}
            className="absolute inset-0 h-full w-full rounded-lg bg-black object-contain"
            onLoadedMetadata={(event) => {
              event.currentTarget.currentTime = selection.start;
            }}
            onTimeUpdate={enforceRange}
            onSeeking={() => {
              enforceRange();
              synchronizeVoice();
            }}
            onSeeked={() => synchronizeVoice(wantsPlaying.current)}
            onPlay={() => {
              const video = videoRef.current;
              if (!video) return;
              if (
                video.currentTime >= selection.end - 0.02 ||
                video.currentTime < selection.start
              )
                video.currentTime = selection.start;
              wantsPlaying.current = true;
              cancelFrame();
              frame.current = requestAnimationFrame(animate);
            }}
            onPlaying={() => {
              picturePlaying.current = true;
              synchronizeVoice(true);
            }}
            onWaiting={() => {
              picturePlaying.current = false;
              audioRef.current?.pause();
            }}
            onPause={() => {
              wantsPlaying.current = false;
              picturePlaying.current = false;
              playRequest.current += 1;
              audioRef.current?.pause();
              cancelFrame();
            }}
            onEnded={() => {
              wantsPlaying.current = false;
              picturePlaying.current = false;
              audioRef.current?.pause();
              cancelFrame();
            }}
            onRateChange={() => synchronizeVoice()}
            onVolumeChange={(event) => {
              if (muteVideo && !event.currentTarget.muted)
                event.currentTarget.muted = true;
              if (audioRef.current)
                audioRef.current.volume = event.currentTarget.volume;
            }}
            onError={() => {
              wantsPlaying.current = false;
              picturePlaying.current = false;
              audioRef.current?.pause();
              cancelFrame();
              setVideoError(true);
            }}
          />
        )}
      </div>
      {voiceover && voiceoverAsset && (
        <audio
          ref={bindAudio}
          src={voiceoverAsset.url}
          preload="metadata"
          muted={voiceMuted}
          aria-label={`${shot.shot_id} 同步旁白`}
          className="hidden"
          onLoadedMetadata={() => {
            voiceReady.current = true;
            synchronizeVoice(wantsPlaying.current);
          }}
          onTimeUpdate={() => {
            if (
              audioRef.current &&
              audioRef.current.currentTime >= voiceover.end
            )
              audioRef.current.pause();
          }}
          onError={() => {
            voiceReady.current = false;
            wantsPlaying.current = false;
            picturePlaying.current = false;
            playRequest.current += 1;
            videoRef.current?.pause();
            audioRef.current?.pause();
            cancelFrame();
            setAudioError(true);
          }}
        />
      )}
      <figcaption className="space-y-2 text-xs text-muted-foreground">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-mono tabular-nums">
            选段 {selection.start.toFixed(2)}–{selection.end.toFixed(2)}s
          </span>
          <span>本幕 {(selection.end - selection.start).toFixed(1)} 秒</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span>{soundLabel}</span>
          {voiceover && (
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={voiceMuted ? "开启旁白" : "旁白静音"}
              onClick={() => setVoiceMuted(!voiceMuted)}
            >
              {voiceMuted ? <VolumeX /> : <Volume2 />}
            </Button>
          )}
        </div>
        {audioError && (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-2 text-amber-300"
          >
            <span>旁白未能播放。请检查素材文件后重新加载。</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                voiceReady.current = false;
                setAudioError(false);
                audioRef.current?.load();
              }}
            >
              重新加载旁白
            </Button>
          </div>
        )}
      </figcaption>
    </figure>
  );
}
