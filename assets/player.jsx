import React from 'react';
import '@vidstack/react/player/styles/default/theme.css';
import '@vidstack/react/player/styles/default/layouts/audio.css';
import '@vidstack/react/player/styles/default/layouts/video.css';
import { MediaPlayer, MediaProvider, Poster, Captions } from "@vidstack/react";
import { DefaultVideoLayout, defaultLayoutIcons } from '@vidstack/react/player/layouts/default';


function InjectCSS() {
  const css = `
    .media-player[data-view-type="video"] {
      aspect-ratio: 16 / 9;
    }

    .vds-video-layout {
      --video-brand: hsl(0, 0%, 96%);
    }

    .vds-audio-layout {
      --audio-brand: hsl(0, 0%, 96%);
    }

    .plyr {
      --plyr-color-main: hsl(198, 100%, 50%);
    }
    
    .vds-slider-chapters {
      display: none;
    }
    
    .rt-Container {
      align-self: center;
    }
  `;

  return <style dangerouslySetInnerHTML={{ __html: css }} />;
}

async function loadHlsLibrary() {
  const mod = await import('hls.js');
  return mod?.default ?? mod;
}

export function DlhdProxyMediaPlayer({ title, src }) {
  const handleProviderChange = React.useCallback((provider) => {
    if (provider && typeof provider.type === 'string' && provider.type.includes('hls')) {
      provider.library = loadHlsLibrary;
    }
  }, []);

  return (
    <>
      <InjectCSS />
      <MediaPlayer
        title={title}
        src={[
          { src, type: 'application/vnd.apple.mpegurl' },
          { src, type: 'application/x-mpegURL' },
        ]}
        viewType='video'
        streamType='live'
        logLevel='warn'
        playsInline
        autoplay
        muted
        crossOrigin="anonymous"
        onProviderChange={handleProviderChange}
      >
        <MediaProvider>
          <Poster className="vds-poster" />
        </MediaProvider>
        <DefaultVideoLayout
          icons={defaultLayoutIcons}
        />
          <Captions className="vds-captions" />
      </MediaPlayer>
    </>
  );
}
