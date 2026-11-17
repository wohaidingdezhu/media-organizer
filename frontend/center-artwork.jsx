import React from 'react';

export function CenterArtwork({artwork, title, photo, loading = 'lazy'}) {
  return artwork.source
    ? <img src={artwork.source} alt={title} loading={loading} onError={artwork.onError} />
    : <div className="placeholder"><span>{photo ? '▧' : '▷'}</span><small>{artwork.message}</small></div>;
}

export function PreviewRepair({repair, onClick}) {
  return <button type="button" data-preview-repair="true" hidden={repair.hidden} disabled={repair.disabled}
    onClick={onClick}>{repair.label}</button>;
}
