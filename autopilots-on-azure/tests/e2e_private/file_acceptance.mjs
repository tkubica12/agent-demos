export function isFileReply(message, outputName, refusalPattern) {
  return message.attachments?.some((attachment) => attachment.name === outputName) ||
    refusalPattern.test(String(message.text));
}
