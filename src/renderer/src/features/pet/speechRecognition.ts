export interface SpeechRecognitionEventLike {
  results: {
    length: number;
    [index: number]: {
      length: number;
      [index: number]: {
        transcript: string;
      };
    };
  };
}

export function extractSpeechTranscript(event: SpeechRecognitionEventLike): string {
  const chunks: string[] = [];

  for (let resultIndex = 0; resultIndex < event.results.length; resultIndex += 1) {
    const result = event.results[resultIndex];

    for (let transcriptIndex = 0; transcriptIndex < result.length; transcriptIndex += 1) {
      chunks.push(result[transcriptIndex].transcript);
    }
  }

  return chunks.join("").trim();
}
