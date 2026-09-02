// Post-thumbnail video playback: the one deliberate motion exception on this
// site. A [data-thumb-video] element sits paused on a non-blank first frame
// by default and only plays when the viewer actually engages with it —
//   - real hover (desktop, mouse): play on mouseenter, pause+rewind on
//     mouseleave.
//   - no hover (touch/mobile): autoplay while at least half the thumbnail is
//     in view, pause+rewind once it isn't.
// Never both at once — matches the maintainer's call: hover on desktop,
// scroll-into-view on mobile, nothing site-wide.
const SELECTOR = '[data-thumb-video]';

// A freshly-loaded <video> with no `poster` shows a blank/black frame until
// it has something to paint. Nudging currentTime forward once metadata is
// ready gives it a real frame to sit on. This used to be done via a `#t=`
// media-fragment hint on the src, but that fights our own play()/pause()
// calls: the browser's own fragment-seek can pause the video again right
// after our explicit play() resolves (observed as a spurious pause event
// firing a beat later, with currentTime pinned at the fragment's offset). A
// plain JS seek on 'loadedmetadata' has none of that.
function showFirstFrame(video: HTMLVideoElement): void {
  const nudge = () => {
    try {
      video.currentTime = Math.min(0.1, video.duration || 0.1);
    } catch {
      // seeking can throw before the element is fully ready — harmless, the
      // thumbnail just stays blank until play() is triggered.
    }
  };
  if (video.readyState >= 1) nudge();
  else video.addEventListener('loadedmetadata', nudge, { once: true });
}

function wireHover(videos: HTMLVideoElement[]): void {
  for (const video of videos) {
    video.addEventListener('mouseenter', () => {
      video.play().catch(() => {
        // autoplay can be refused for reasons outside our control (e.g. data
        // saver mode) — the thumbnail just stays on its first frame.
      });
    });
    video.addEventListener('mouseleave', () => {
      video.pause();
      video.currentTime = 0;
    });
  }
}

function wireScrollInView(videos: HTMLVideoElement[]): void {
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const video = entry.target as HTMLVideoElement;
        if (entry.isIntersecting) {
          video.play().catch(() => {});
        } else {
          video.pause();
          video.currentTime = 0;
        }
      }
    },
    { threshold: 0.5 }
  );
  for (const video of videos) observer.observe(video);
}

function init(): void {
  const videos = Array.from(document.querySelectorAll<HTMLVideoElement>(SELECTOR));
  if (videos.length === 0) return;

  for (const video of videos) showFirstFrame(video);

  const canHover = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  if (canHover) {
    wireHover(videos);
  } else {
    wireScrollInView(videos);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
